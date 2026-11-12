"""Indexes, repositories, migrations, backup and restore.

These run on mongomock by default and on a real server when TEST_MONGODB_URI
is set. Tests marked ``real_mongo`` check behaviour only a real server has
(query plans, TTL index options) and are skipped otherwise.
"""

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.log_repository import ApiLogRepository, AuditLogRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.prediction_repository import PredictionRepository
from app.repositories.user_repository import DuplicateEmailError, UserRepository
from app.services.backup_service import BackupError, BackupService
from migrations.runner import (
    Migration,
    MigrationError,
    MigrationLockedError,
    MigrationRunner,
    discover,
)
from scripts import api_stats, backup_database, migrate, restore_database
from tests.conftest import assert_success, predict_json, register_and_login

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _prediction(user_id, minutes=0, **extra):
    document = {
        "schema_version": 2,
        "user_id": user_id,
        "inputs": {"nitrogen": 90.0, "phosphorus": 42.0, "potassium": 43.0, "temperature": 20.8, "humidity": 82.0, "ph": 6.5, "rainfall": 202.9},
        "recommended_crop": "rice",
        "confidence": 0.9,
        "alternatives": [],
        "out_of_range_fields": [],
        "created_at": NOW + timedelta(minutes=minutes),
    }
    document.update(extra)
    return document


# ==================================================================== indexes
EXPECTED_INDEXES = {
    "users": {"uniq_email"},
    "predictions": {"user_created"},
    "password_reset_tokens": {"uniq_token_hash", "ttl_expires_at", "user_id"},
    "audit_logs": {"created_desc", "event_created"},
    "api_logs": {"ttl_created", "endpoint_created"},
    "backups": {"uniq_backup_id", "created_desc"},
    "migration_history": {"uniq_version"},
}


@pytest.mark.parametrize("collection,names", EXPECTED_INDEXES.items())
def test_expected_indexes_exist(db, collection, names):
    assert names <= set(db[collection].index_information())


def test_index_definitions(db):
    users = db["users"].index_information()["uniq_email"]
    assert users["key"] == [("email", 1)] and users["unique"] is True
    predictions = db["predictions"].index_information()["user_created"]
    assert predictions["key"] == [("user_id", 1), ("created_at", -1)]
    assert db["password_reset_tokens"].index_information()["uniq_token_hash"]["unique"] is True
    assert db["password_reset_tokens"].index_information()["ttl_expires_at"]["expireAfterSeconds"] == 0
    assert db["api_logs"].index_information()["ttl_created"]["expireAfterSeconds"] == 14 * 24 * 3600
    assert db["backups"].index_information()["uniq_backup_id"]["unique"] is True


def test_email_uniqueness_is_enforced_by_the_database(db):
    db["users"].insert_one({"email": "dup@example.test", "name": "A"})
    with pytest.raises(DuplicateKeyError):
        db["users"].insert_one({"email": "dup@example.test", "name": "B"})


@pytest.mark.real_mongo
@pytest.mark.parametrize(
    "collection,query,sort,index",
    [
        ("users", {"email": "asha@example.test"}, None, "uniq_email"),
        ("predictions", {"user_id": ObjectId()}, [("created_at", -1), ("_id", -1)], "user_created"),
        ("password_reset_tokens", {"token_hash": "x" * 64}, None, "uniq_token_hash"),
        ("audit_logs", {"event": "auth.login_failure"}, [("created_at", -1)], "event_created"),
        ("audit_logs", {}, [("created_at", -1)], "created_desc"),
        ("backups", {"backup_id": "x"}, None, "uniq_backup_id"),
    ],
)
def test_frequent_queries_use_an_index_not_a_collection_scan(db, collection, query, sort, index):
    # Filler rows carry distinct values for every uniquely indexed field.
    db[collection].insert_many(
        [{"email": f"u{i}@example.test", "token_hash": f"hash-{i}", "backup_id": f"backup-{i}"} for i in range(50)]
    )
    cursor = db[collection].find(query)
    if sort:
        cursor = cursor.sort(sort)
    plan = json.dumps(cursor.explain()["queryPlanner"]["winningPlan"])
    assert "COLLSCAN" not in plan, plan
    assert "IXSCAN" in plan and index in plan


@pytest.mark.real_mongo
def test_history_page_query_examines_only_the_users_documents(db):
    mine, other = ObjectId(), ObjectId()
    db["predictions"].insert_many([_prediction(mine, i) for i in range(5)] + [_prediction(other, i) for i in range(200)])
    stats = (
        db["predictions"].find({"user_id": mine}).sort([("created_at", -1), ("_id", -1)]).limit(10).explain()["executionStats"]
    )
    assert stats["nReturned"] == 5
    assert stats["totalDocsExamined"] == 5
    assert stats["totalKeysExamined"] <= 6


@pytest.mark.real_mongo
def test_fetching_one_prediction_uses_the_id_index(db):
    document_id = db["predictions"].insert_one(_prediction(ObjectId())).inserted_id
    stats = db["predictions"].find({"_id": document_id, "user_id": ObjectId()}).explain()["executionStats"]
    assert stats["totalDocsExamined"] <= 1


# =============================================================== repositories
def test_user_repository(db):
    users = UserRepository(lambda: db)
    created = users.create(name="Asha", email="asha@example.test", password_hash="scrypt:hash")
    assert created["session_version"] == 1 and created["created_at"].tzinfo is not None

    assert users.find_by_email("asha@example.test")["password_hash"] == "scrypt:hash"
    assert users.find_by_email("ASHA@example.test") is None  # callers normalise first
    public = users.find_by_id(created["_id"])
    assert "password_hash" not in public and public["email"] == "asha@example.test"
    assert users.find_by_id(ObjectId()) is None

    with pytest.raises(DuplicateEmailError):
        users.create(name="Other", email="asha@example.test", password_hash="x")

    assert users.update_password(created["_id"], "scrypt:new") is True
    updated = users.find_by_email("asha@example.test")
    assert updated["password_hash"] == "scrypt:new" and updated["session_version"] == 2
    assert users.update_password(ObjectId(), "x") is False

    users.record_login(created["_id"])
    assert users.find_by_email("asha@example.test")["last_login_at"] is not None


def test_prediction_repository_scopes_everything_by_owner(db):
    predictions = PredictionRepository(lambda: db)
    mine, other = ObjectId(), ObjectId()
    ids = [db["predictions"].insert_one(_prediction(mine, i)).inserted_id for i in range(12)]
    other_id = db["predictions"].insert_one(_prediction(other)).inserted_id

    items, total = predictions.list_for_user(mine, page=1, per_page=5)
    assert total == 12 and [item["_id"] for item in items] == ids[::-1][:5]
    items, _ = predictions.list_for_user(mine, page=3, per_page=5)
    assert [item["_id"] for item in items] == ids[::-1][10:]
    assert predictions.list_for_user(ObjectId(), page=1, per_page=5) == ([], 0)

    assert predictions.find_for_user(other_id, mine) is None
    assert predictions.find_for_user(other_id, other)["_id"] == other_id
    assert predictions.delete_for_user(other_id, mine) is False
    assert db["predictions"].count_documents({"_id": other_id}) == 1
    assert predictions.delete_for_user(other_id, other) is True

    recent = predictions.recent_inputs_for_user(mine, limit=4)
    assert [doc["_id"] for doc in recent] == ids[-4:]  # oldest first


def test_prediction_repository_create(db):
    predictions = PredictionRepository(lambda: db)
    document = predictions.create(
        user_id=ObjectId(), inputs={"ph": 6.5}, input_sources={"ph": "manual"}, weather=None,
        recommended_crop="rice", confidence=0.9, alternatives=[], out_of_range_fields=[], model={"n": 1},
    )
    stored = db["predictions"].find_one({"_id": document["_id"]})
    assert stored["schema_version"] == 2 and stored["created_at"].tzinfo is not None


def test_password_reset_repository(db):
    tokens = PasswordResetRepository(lambda: db)
    user = ObjectId()
    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    tokens.create(user_id=user, token_hash="a" * 64, expires_at=future)
    tokens.create(user_id=user, token_hash="b" * 64, expires_at=future)  # replaces the first
    assert tokens.find_valid("a" * 64) is None
    assert tokens.find_valid("b" * 64)["user_id"] == user
    assert tokens.consume("b" * 64)["user_id"] == user
    assert tokens.consume("b" * 64) is None

    tokens.create(user_id=user, token_hash="c" * 64, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert tokens.find_valid("c" * 64) is None and tokens.consume("c" * 64) is None


def test_log_repositories(db):
    audit = AuditLogRepository(lambda: db)
    audit.insert({"event": "auth.login_failure", "outcome": "failure"})
    audit.insert({"event": "auth.signup", "outcome": "success"})
    assert len(audit.recent()) == 2
    assert [e["event"] for e in audit.recent(event="auth.signup")] == ["auth.signup"]

    api = ApiLogRepository(lambda: db)
    db["api_logs"].insert_many(
        [
            {"endpoint": "predictions.create", "method": "POST", "status": 201, "duration_ms": 10.0, "created_at": NOW},
            {"endpoint": "predictions.create", "method": "POST", "status": 500, "duration_ms": 30.0, "created_at": NOW},
            {"endpoint": "health.ready", "method": "GET", "status": 200, "duration_ms": 2.0, "created_at": NOW},
        ]
    )
    rows = {row["_id"]["endpoint"]: row for row in api.latency_by_endpoint()}
    assert rows["predictions.create"]["count"] == 2
    assert rows["predictions.create"]["avg_ms"] == 20.0 and rows["predictions.create"]["max_ms"] == 30.0
    assert rows["predictions.create"]["errors"] == 1
    assert api.latency_by_endpoint(since=NOW + timedelta(days=1)) == []


# ================================================================= migrations
def test_migrations_are_discovered_in_order():
    migrations = discover()
    assert [m.version for m in migrations] == [1, 2, 3, 4]
    assert [m.name for m in migrations] == [
        "001_create_indexes", "002_normalize_users", "003_prediction_metadata", "004_remove_government_ids",
    ]
    assert [m.reversible for m in migrations] == [True, True, True, False]
    assert all(m.description for m in migrations)


def test_fresh_database_upgrade_records_history(raw_db):
    runner = MigrationRunner(raw_db)
    assert [m.version for m in runner.pending()] == [1, 2, 3, 4]
    results = runner.upgrade()
    assert [(r["version"], r["status"]) for r in results] == [(1, "applied"), (2, "applied"), (3, "applied"), (4, "applied")]
    assert runner.pending() == []

    history = list(raw_db["migration_history"].find({"version": {"$gt": 0}}).sort("version", 1))
    assert [h["version"] for h in history] == [1, 2, 3, 4]
    for record in history:
        assert record["status"] == "applied"
        assert record["applied_at"].tzinfo is not None and record["duration_ms"] >= 0
        assert record["description"] and record["events"][0]["direction"] == "up"
    assert raw_db["migration_history"].find_one({"_id": "lock"}) is None  # lock released


def test_running_migrations_again_is_a_no_op(raw_db):
    runner = MigrationRunner(raw_db)
    runner.upgrade()
    before = list(raw_db["migration_history"].find({}).sort("version", 1))
    assert runner.upgrade() == []
    assert list(raw_db["migration_history"].find({}).sort("version", 1)) == before
    assert all(row["status"] == "applied" for row in runner.status())


def test_index_migration_is_idempotent_when_reapplied(raw_db):
    module = __import__("importlib").import_module("migrations.001_create_indexes")
    first = module.up(raw_db)
    second = module.up(raw_db)
    assert first == second
    assert module.plan(raw_db)["indexes_to_create"] == []


def test_upgrade_to_a_target_version(raw_db):
    runner = MigrationRunner(raw_db)
    assert [r["version"] for r in runner.upgrade(target=2)] == [1, 2]
    assert [m.version for m in runner.pending()] == [3, 4]


def test_dry_run_changes_nothing(raw_db):
    raw_db["users"].insert_one({"email": "Mixed@Example.TEST", "password": "scrypt:x", "name": "A"})
    results = MigrationRunner(raw_db).upgrade(dry_run=True)
    assert [r["status"] for r in results] == ["would_apply"] * 4
    assert results[1]["plan"]["affected_documents"] == 1
    assert raw_db["users"].find_one({})["email"] == "Mixed@Example.TEST"
    assert raw_db["migration_history"].count_documents({}) == 0
    assert "uniq_email" not in raw_db["users"].index_information()


def _legacy_database(raw_db):
    """Data shaped the way the original application wrote it."""
    user_id = raw_db["users"].insert_one(
        {
            "name": "Legacy Farmer",
            "email": "  Legacy@Example.TEST ",
            "password": "scrypt:legacy-hash",
            "govt_id_type": "aadhaar",
            "govt_id": "1234-5678-9012",
            "phone": "9999999999",
            "created_at": datetime(2025, 7, 1),
        }
    ).inserted_id
    raw_db["predictions"].insert_one(
        {
            "user_id": str(user_id),
            "inputs": {"N": 90.0, "P": 42.0, "K": 43.0, "temp": 20.8, "hum": 82.0, "ph": 6.5, "rain": 202.9},
            "recommended_crop": "rice",
            "timestamp": datetime(2025, 7, 2),
        }
    )
    return user_id


def test_legacy_users_and_predictions_are_upgraded(raw_db):
    user_id = _legacy_database(raw_db)
    MigrationRunner(raw_db).upgrade(target=3)

    user = raw_db["users"].find_one({"_id": user_id})
    assert user["email"] == "legacy@example.test"
    assert user["_legacy"]["email"] == "  Legacy@Example.TEST "
    assert user["password_hash"] == "scrypt:legacy-hash" and "password" not in user
    assert user["session_version"] == 1
    assert user["govt_id"] == "1234-5678-9012"  # untouched until migration 004 is explicitly allowed

    prediction = raw_db["predictions"].find_one({})
    assert prediction["user_id"] == user_id and isinstance(prediction["user_id"], ObjectId)
    assert prediction["inputs"] == {"nitrogen": 90.0, "phosphorus": 42.0, "potassium": 43.0, "temperature": 20.8, "humidity": 82.0, "ph": 6.5, "rainfall": 202.9}
    assert prediction["schema_version"] == 2 and "timestamp" not in prediction
    assert prediction["created_at"].replace(tzinfo=None) == datetime(2025, 7, 2)
    assert prediction["confidence"] is None and prediction["alternatives"] == []


def test_upgraded_legacy_data_works_through_the_api(app_factory, mongo_client, db_name):
    """A legacy user can log in and open their old analysis after migrating."""
    from werkzeug.security import generate_password_hash

    raw = mongo_client[db_name]
    user_id = raw["users"].insert_one({"name": "Legacy Farmer", "email": "Legacy@Example.TEST", "password": generate_password_hash("Old-Passw0rd-Here")}).inserted_id
    raw["predictions"].insert_one({"user_id": str(user_id), "inputs": {"N": 90, "P": 42, "K": 43, "temp": 20.8, "hum": 82, "ph": 6.5, "rain": 202.9}, "recommended_crop": "rice", "timestamp": datetime(2025, 7, 2)})

    app = app_factory(db_name=db_name)  # AUTO_MIGRATE applies 001-004 on startup
    client = app.test_client()
    assert_success(client.post("/api/auth/login", json={"email": "legacy@example.test", "password": "Old-Passw0rd-Here"}))
    items = assert_success(client.get("/api/predictions"))["items"]
    assert len(items) == 1 and items[0]["confidence"] is None
    detail = assert_success(client.get(f"/api/predictions/{items[0]['id']}"))
    assert detail["recommended_crop"] == "rice" and detail["soil_analysis"]["total_readings"] == 7


def test_email_collision_aborts_normalisation_without_changing_data(raw_db):
    raw_db["users"].insert_many([{"email": "Same@Example.test", "password": "a"}, {"email": "same@example.test", "password": "b"}])
    runner = MigrationRunner(raw_db)
    with pytest.raises(MigrationError, match="would be shared by more than one account"):
        runner.upgrade()
    assert sorted(u["email"] for u in raw_db["users"].find({})) == ["Same@Example.test", "same@example.test"]
    assert all("password" in u for u in raw_db["users"].find({}))
    record = raw_db["migration_history"].find_one({"version": 2})
    assert record["status"] == "failed" and record["events"][-1]["error"]
    assert [m.version for m in runner.pending()] == [2, 3, 4]
    assert raw_db["migration_history"].find_one({"_id": "lock"}) is None


def test_duplicate_emails_block_the_unique_index_with_a_clear_message(raw_db):
    raw_db["users"].insert_many([{"email": "dup@example.test"}, {"email": "dup@example.test"}])
    with pytest.raises(MigrationError, match="used by more than one account"):
        MigrationRunner(raw_db).upgrade()


def test_irreversible_migration_is_blocked_until_explicitly_allowed(raw_db):
    user_id = _legacy_database(raw_db)
    runner = MigrationRunner(raw_db)
    results = runner.upgrade()
    assert [(r["version"], r["status"]) for r in results] == [(1, "applied"), (2, "applied"), (3, "applied"), (4, "blocked")]
    assert "cannot be undone" in results[-1]["reason"] and results[-1]["plan"]["affected_documents"] == 1
    assert raw_db["users"].find_one({"_id": user_id})["govt_id"] == "1234-5678-9012"
    assert [m.version for m in runner.pending()] == [4]

    results = runner.upgrade(allow_irreversible=True)
    assert results[0]["status"] == "applied" and results[0]["summary"]["users_updated"] == 1
    user = raw_db["users"].find_one({"_id": user_id})
    assert not {"govt_id", "govt_id_type", "phone"} & set(user)
    assert user["name"] == "Legacy Farmer" and user["email"] == "legacy@example.test"


def test_irreversible_migration_runs_freely_when_there_is_nothing_to_destroy(raw_db):
    results = MigrationRunner(raw_db).upgrade()
    assert results[-1] ["status"] == "applied" and results[-1]["summary"]["users_updated"] == 0


def test_rollback_restores_the_legacy_shape(raw_db):
    user_id = _legacy_database(raw_db)
    runner = MigrationRunner(raw_db)
    runner.upgrade(target=3)
    results = runner.downgrade(target=0)
    assert [(r["version"], r["status"]) for r in results] == [(3, "rolled_back"), (2, "rolled_back"), (1, "rolled_back")]

    user = raw_db["users"].find_one({"_id": user_id})
    assert user["email"] == "  Legacy@Example.TEST " and user["password"] == "scrypt:legacy-hash"
    assert "password_hash" not in user and "_legacy" not in user
    prediction = raw_db["predictions"].find_one({})
    assert prediction["user_id"] == str(user_id)
    assert prediction["inputs"] == {"N": 90.0, "P": 42.0, "K": 43.0, "temp": 20.8, "hum": 82.0, "ph": 6.5, "rain": 202.9}
    assert "schema_version" not in prediction and "timestamp" in prediction
    assert "uniq_email" not in raw_db["users"].index_information()

    statuses = {row["version"]: row["status"] for row in runner.status()}
    assert statuses == {1: "rolled_back", 2: "rolled_back", 3: "rolled_back", 4: "pending"}
    assert [e["direction"] for e in raw_db["migration_history"].find_one({"version": 2})["events"]] == ["up", "down"]


def test_rollback_leaves_new_format_predictions_alone(raw_db):
    runner = MigrationRunner(raw_db)
    runner.upgrade(target=3)
    new_id = raw_db["predictions"].insert_one(_prediction(ObjectId())).inserted_id
    runner.downgrade(target=2)
    assert raw_db["predictions"].find_one({"_id": new_id})["schema_version"] == 2


def test_cannot_roll_back_past_an_irreversible_migration(raw_db):
    runner = MigrationRunner(raw_db)
    runner.upgrade()
    with pytest.raises(MigrationError, match="irreversible"):
        runner.downgrade(target=3)
    assert runner.pending() == []


def test_concurrent_migration_runs_are_prevented(raw_db):
    runner = MigrationRunner(raw_db)
    runner._acquire_lock()
    with pytest.raises(MigrationLockedError, match="in progress"):
        MigrationRunner(raw_db).upgrade()
    assert MigrationRunner(raw_db).applied_versions() == set()
    assert runner.force_unlock() is True and runner.force_unlock() is False
    assert len(MigrationRunner(raw_db).upgrade()) == 4


def test_a_failing_migration_stops_the_run_and_is_recorded(raw_db):
    def explode(_db):
        raise ValueError("boom")

    ok = Migration(1, "001_ok", "fine", True, lambda db: {"affected_documents": 0}, lambda db: {"done": 1}, lambda db: {})
    bad = Migration(2, "002_bad", "fails", True, lambda db: {"affected_documents": 0}, explode, lambda db: {})
    never = Migration(3, "003_never", "not reached", True, lambda db: {"affected_documents": 0}, lambda db: {"done": 1}, lambda db: {})
    runner = MigrationRunner(raw_db, [ok, bad, never])
    with pytest.raises(MigrationError, match="002_bad"):
        runner.upgrade()
    statuses = {row["version"]: row["status"] for row in runner.status()}
    assert statuses == {1: "applied", 2: "failed", 3: "pending"}
    assert raw_db["migration_history"].find_one({"_id": "lock"}) is None


def test_migrate_cli(raw_db, capsys):
    assert migrate.main(["status"], db=raw_db) == 0
    assert '"status": "pending"' in capsys.readouterr().out

    assert migrate.main(["up", "--dry-run"], db=raw_db) == 0
    assert "no changes were made" in capsys.readouterr().out
    assert raw_db["migration_history"].count_documents({}) == 0

    assert migrate.main(["up"], db=raw_db) == 0
    assert raw_db["audit_logs"].count_documents({"event": "migration.applied"}) == 4
    capsys.readouterr()
    assert migrate.main(["up"], db=raw_db) == 0
    assert "up to date" in capsys.readouterr().out

    assert migrate.main(["down", "--to", "3"], db=raw_db) == 1  # needs --yes
    assert migrate.main(["down", "--to", "3", "--yes"], db=raw_db) == 1  # 004 is irreversible
    assert "irreversible" in capsys.readouterr().err
    assert migrate.main(["unlock"], db=raw_db) == 0


def test_migrate_cli_reports_a_blocked_irreversible_migration(raw_db, capsys):
    _legacy_database(raw_db)
    assert migrate.main(["up"], db=raw_db) == 2
    assert "--allow-irreversible" in capsys.readouterr().err
    assert migrate.main(["up", "--allow-irreversible"], db=raw_db) == 0
    assert "govt_id" not in raw_db["users"].find_one({})


# ============================================================ backup / restore
@pytest.fixture
def populated(app, db):
    """A migrated database holding two users and three analyses."""
    first = register_and_login(app)
    second = register_and_login(app, name="Ravi Kumar", email="ravi@example.test", password="Orch4rd-Gate-Pasture")
    predict_json(first)
    predict_json(first, nitrogen=80)
    predict_json(second)
    return db


@pytest.fixture
def backups(populated, tmp_path):
    return BackupService(populated, tmp_path / "bk")


def _snapshot(database, names=("users", "predictions", "audit_logs", "migration_history")):
    return {name: list(database[name].find({}).sort("_id", 1)) for name in names}


def test_backup_creates_files_manifest_and_metadata(backups, populated, tmp_path):
    metadata = backups.create_backup(note="unit test")
    assert metadata["status"] == "completed"
    assert metadata["collections"] == ["users", "predictions", "audit_logs", "migration_history"]
    assert metadata["record_counts"]["users"] == 2 and metadata["record_counts"]["predictions"] == 3
    assert metadata["record_counts"]["migration_history"] == 4
    assert metadata["record_counts"]["audit_logs"] == populated["audit_logs"].count_documents({})
    assert metadata["created_at"].tzinfo is not None and metadata["completed_at"] >= metadata["created_at"]
    assert len(metadata["checksum"]) == 64 and metadata["size_bytes"] > 0 and metadata["note"] == "unit test"

    directory = tmp_path / "bk" / metadata["backup_id"]
    assert str(directory.resolve()) == metadata["location"]
    assert sorted(p.name for p in directory.iterdir()) == ["audit_logs.jsonl.gz", "manifest.json", "migration_history.jsonl.gz", "predictions.jsonl.gz", "users.jsonl.gz"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backup_id"] == metadata["backup_id"] and manifest["total_documents"] == sum(metadata["record_counts"].values())
    assert {index["name"] for index in manifest["collections"]["users"]["indexes"]} == {"uniq_email"}
    assert manifest["collections"]["users"]["indexes"][0]["unique"] is True

    with gzip.open(directory / "users.jsonl.gz", "rt", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 2 and '"$oid"' in lines[0]

    stored = populated["backups"].find_one({"backup_id": metadata["backup_id"]})
    assert stored["status"] == "completed" and stored["record_counts"] == metadata["record_counts"]
    assert not list((tmp_path / "bk").glob("*.partial"))


def test_backups_never_overwrite_each_other(backups, tmp_path):
    first = backups.create_backup()
    second = backups.create_backup()
    assert first["backup_id"] != second["backup_id"]
    assert len([p for p in (tmp_path / "bk").iterdir() if p.is_dir()]) == 2
    assert [b["backup_id"] for b in backups.list_backups()][:2] == sorted([first["backup_id"], second["backup_id"]], reverse=True) or len(backups.list_backups()) == 2


def test_restore_into_an_empty_database_reproduces_the_data_exactly(backups, populated, mongo_client, tmp_path):
    metadata = backups.create_backup()
    target = mongo_client["agri_test_restore_target"]
    mongo_client.drop_database(target.name)
    try:
        result = BackupService(target, tmp_path / "bk").restore(metadata["location"])
        assert result["record_counts"] == metadata["record_counts"]
        assert _snapshot(target) == _snapshot(populated)

        user = target["users"].find_one({})
        assert isinstance(user["_id"], ObjectId) and user["created_at"].tzinfo is not None
        assert isinstance(target["predictions"].find_one({})["user_id"], ObjectId)

        assert target["users"].index_information()["uniq_email"]["unique"] is True
        assert target["predictions"].index_information()["user_created"]["key"] == [("user_id", 1), ("created_at", -1)]
        with pytest.raises(DuplicateKeyError):
            target["users"].insert_one({"email": user["email"]})
        assert not [name for name in target.list_collection_names() if name.endswith("__restore_tmp")]
    finally:
        mongo_client.drop_database(target.name)


def test_restore_refuses_to_overwrite_existing_data(backups, populated):
    metadata = backups.create_backup()
    populated["users"].update_one({"email": "asha@example.test"}, {"$set": {"name": "Changed Since Backup"}})
    before = _snapshot(populated)
    with pytest.raises(BackupError, match="already contain data"):
        backups.restore(metadata["location"])
    assert _snapshot(populated) == before


def test_restore_with_drop_existing_replaces_the_data(backups, populated):
    metadata = backups.create_backup()
    original = _snapshot(populated)
    populated["users"].update_one({"email": "asha@example.test"}, {"$set": {"name": "Changed Since Backup"}})
    populated["predictions"].delete_many({})
    populated["users"].insert_one({"email": "new@example.test", "name": "Added Later"})

    backups.restore(metadata["location"], drop_existing=True)
    assert _snapshot(populated) == original
    assert populated["users"].index_information()["uniq_email"]["unique"] is True


def test_corrupt_backup_is_detected_before_anything_changes(backups, populated, tmp_path):
    metadata = backups.create_backup()
    directory = tmp_path / "bk" / metadata["backup_id"]
    data = bytearray((directory / "users.jsonl.gz").read_bytes())
    data[-5] ^= 0xFF
    (directory / "users.jsonl.gz").write_bytes(bytes(data))
    before = _snapshot(populated)
    with pytest.raises(BackupError, match="checksum"):
        backups.restore(directory, drop_existing=True)
    assert _snapshot(populated) == before


def test_tampered_manifest_is_detected(backups, tmp_path):
    metadata = backups.create_backup()
    manifest_path = tmp_path / "bk" / metadata["backup_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["collections"]["users"]["documents"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="does not match the checksum recorded"):
        backups.verify_backup(manifest_path.parent)


@pytest.mark.parametrize("mutate", [lambda d: (d / "manifest.json").unlink(), lambda d: (d / "predictions.jsonl.gz").unlink(), lambda d: (d / "manifest.json").write_text("{broken", encoding="utf-8")])
def test_incomplete_backups_are_rejected(backups, tmp_path, mutate):
    metadata = backups.create_backup()
    directory = tmp_path / "bk" / metadata["backup_id"]
    mutate(directory)
    with pytest.raises(BackupError):
        backups.verify_backup(directory)


def test_manifest_cannot_point_outside_the_backup_directory(backups, populated, tmp_path):
    metadata = backups.create_backup()
    populated["backups"].update_one({"backup_id": metadata["backup_id"]}, {"$set": {"checksum": None}})
    manifest_path = tmp_path / "bk" / metadata["backup_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["collections"]["users"]["file"] = "../../outside.jsonl.gz"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="unsafe file name"):
        backups.verify_backup(manifest_path.parent)


@pytest.mark.parametrize("bad_id", ["", "../etc", "a/b", "a\\b", "..", "missing-id"])
def test_backup_ids_cannot_be_used_for_path_traversal(backups, bad_id):
    with pytest.raises(BackupError):
        backups.resolve_backup_path(bad_id)


def test_failed_backup_is_recorded_and_leaves_no_partial_files(backups, populated, tmp_path, monkeypatch):
    def fail(self, name, directory):
        if name == "predictions":
            raise OSError("disk full")
        return original(self, name, directory)

    original = BackupService._export_collection
    monkeypatch.setattr(BackupService, "_export_collection", fail)
    with pytest.raises(BackupError, match="failed"):
        backups.create_backup()

    record = populated["backups"].find_one({})
    assert record["status"] == "failed" and record["error_type"] == "OSError" and record["completed_at"] is not None
    assert record["checksum"] is None
    assert not (tmp_path / "bk").exists() or list((tmp_path / "bk").iterdir()) == []


def test_backup_and_restore_cli(populated, tmp_path, capsys):
    backup_dir = str(tmp_path / "cli")
    assert backup_database.main(["--note", "cli"], db=populated, backup_dir=backup_dir) == 0
    backup_id = populated["backups"].find_one({"note": "cli"})["backup_id"]
    assert backup_id in capsys.readouterr().out
    assert populated["audit_logs"].count_documents({"event": "backup.created"}) == 1
    assert backup_database.main(["--list"], db=populated, backup_dir=backup_dir) == 0
    assert backup_id in capsys.readouterr().out

    assert restore_database.main(["--backup-id", backup_id, "--verify-only"], db=populated, backup_dir=backup_dir) == 0
    assert "all checksums match" in capsys.readouterr().out

    populated["users"].update_one({"email": "asha@example.test"}, {"$set": {"name": "Changed"}})

    # No confirmation, wrong confirmation, and non-empty target are all refused.
    assert restore_database.main(["--backup-id", backup_id], db=populated, backup_dir=backup_dir) == 1
    assert "not confirmed" in capsys.readouterr().err
    assert restore_database.main(["--backup-id", backup_id, "--confirm", "wrong_name"], db=populated, backup_dir=backup_dir) == 1
    assert restore_database.main(["--backup-id", backup_id, "--confirm", populated.name], db=populated, backup_dir=backup_dir) == 1
    assert "already contain data" in capsys.readouterr().err
    assert populated["users"].find_one({"email": "asha@example.test"})["name"] == "Changed"
    assert populated["audit_logs"].count_documents({"event": "restore.failed"}) == 1

    assert restore_database.main(["--backup-id", backup_id, "--confirm", populated.name, "--drop-existing"], db=populated, backup_dir=backup_dir) == 0
    assert "Safety backup of current data created" in capsys.readouterr().out
    assert populated["users"].find_one({"email": "asha@example.test"})["name"] == "Asha Patel"
    assert populated["backups"].count_documents({"status": "completed"}) == 2  # original + safety backup
    # The audit log itself was restored, then the outcome of this restore was appended.
    assert populated["audit_logs"].count_documents({"event": "restore.completed"}) == 1

    assert restore_database.main(["--backup-id", "does-not-exist", "--confirm", populated.name], db=populated, backup_dir=backup_dir) == 1


def test_backup_never_exposes_secrets_in_metadata(backups, populated):
    metadata = backups.create_backup()
    text = str(metadata) + str(populated["backups"].find_one({}))
    assert "scrypt:" not in text and "password" not in text.lower()


def test_api_stats_cli(populated, capsys):
    assert api_stats.main(["--hours", "1"], db=populated) == 0
    output = capsys.readouterr().out
    assert "predictions.create" in output and "auth.login" in output
