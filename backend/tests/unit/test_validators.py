"""Server-side input validation."""

import pytest
from bson import ObjectId

from app.errors import NotFoundError, ValidationError
from app.models.features import FEATURES
from app.utils.validators import (
    normalize_email,
    parse_number,
    parse_object_id,
    password_problem,
    validate_city,
    validate_login,
    validate_new_password,
    validate_pagination,
    validate_prediction_inputs,
    validate_signup,
)
from tests.conftest import RICE_INPUTS, TEST_PASSWORD


# ---------------------------------------------------------------- prediction
def test_valid_inputs_are_returned_as_floats():
    values, out_of_range = validate_prediction_inputs(RICE_INPUTS)
    assert values == {key: float(value) for key, value in RICE_INPUTS.items()}
    assert out_of_range == []


def test_string_inputs_from_a_form_are_accepted():
    values, _ = validate_prediction_inputs({key: f" {value} " for key, value in RICE_INPUTS.items()})
    assert values["ph"] == 6.5


@pytest.mark.parametrize("field", [spec.key for spec in FEATURES])
def test_each_missing_field_is_reported(field):
    payload = dict(RICE_INPUTS)
    del payload[field]
    with pytest.raises(ValidationError) as excinfo:
        validate_prediction_inputs(payload)
    assert list(excinfo.value.details) == [field]
    assert "is required" in excinfo.value.details[field]


def test_all_errors_are_reported_together():
    with pytest.raises(ValidationError) as excinfo:
        validate_prediction_inputs({})
    assert set(excinfo.value.details) == {spec.key for spec in FEATURES}
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "bad",
    [
        "abc",
        "12abc",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "1e3",
        "1e309",
        "1_000",
        "0x10",
        "١٢",
        "1,5",
        "1.2.3",
        "--5",
        "5-",
        "9" * 13,
        "0.1234567",
        True,
        False,
        None,
        "",
        "   ",
        [],
        [90],
        {},
        {"$gt": 0},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_non_numeric_and_non_finite_values_are_rejected(bad):
    payload = dict(RICE_INPUTS, nitrogen=bad)
    with pytest.raises(ValidationError) as excinfo:
        validate_prediction_inputs(payload)
    assert "nitrogen" in excinfo.value.details


@pytest.mark.parametrize("spec", FEATURES, ids=lambda spec: spec.key)
def test_values_outside_the_hard_range_are_rejected(spec):
    for value in (spec.hard_min - 0.01, spec.hard_max + 0.01, -1e9, 1e9):
        with pytest.raises(ValidationError) as excinfo:
            validate_prediction_inputs(dict(RICE_INPUTS, **{spec.key: value}))
        assert "must be between" in excinfo.value.details[spec.key]


@pytest.mark.parametrize("spec", FEATURES, ids=lambda spec: spec.key)
def test_hard_range_boundaries_are_accepted(spec):
    for value in (spec.hard_min, spec.hard_max):
        values, _ = validate_prediction_inputs(dict(RICE_INPUTS, **{spec.key: value}))
        assert values[spec.key] == float(value)


@pytest.mark.parametrize("field", ["nitrogen", "phosphorus", "potassium", "humidity", "ph", "rainfall"])
def test_negative_values_are_rejected_where_meaningless(field):
    with pytest.raises(ValidationError) as excinfo:
        validate_prediction_inputs(dict(RICE_INPUTS, **{field: -1}))
    assert field in excinfo.value.details


def test_negative_temperature_is_physically_possible():
    values, out_of_range = validate_prediction_inputs(dict(RICE_INPUTS, temperature=-5))
    assert values["temperature"] == -5.0
    assert out_of_range == ["temperature"]


def test_ph_error_message_is_human_readable():
    with pytest.raises(ValidationError) as excinfo:
        validate_prediction_inputs(dict(RICE_INPUTS, ph=15))
    assert excinfo.value.details["ph"] == "Soil pH must be between 0 and 14."


@pytest.mark.parametrize("spec", FEATURES, ids=lambda spec: spec.key)
def test_values_outside_the_model_range_are_accepted_but_flagged(spec):
    """Physically possible but outside what the model was trained on."""
    candidates = []
    if spec.model_max < spec.hard_max:
        candidates.append((spec.model_max + spec.hard_max) / 2)
    if spec.model_min > spec.hard_min:
        candidates.append((spec.model_min + spec.hard_min) / 2)
    assert candidates, f"{spec.key} has no gap between model and hard range"
    for value in candidates:
        values, out_of_range = validate_prediction_inputs(dict(RICE_INPUTS, **{spec.key: value}))
        assert values[spec.key] == value
        assert out_of_range == [spec.key]


def test_payload_must_be_a_mapping():
    with pytest.raises(ValidationError):
        validate_prediction_inputs([1, 2, 3])


def test_unknown_extra_fields_are_ignored():
    values, _ = validate_prediction_inputs(dict(RICE_INPUTS, is_admin=True, user_id="someone-else"))
    assert set(values) == {spec.key for spec in FEATURES}


@pytest.mark.parametrize("raw,expected", [("12", 12.0), (" 6.5 ", 6.5), ("+4.25", 4.25), (".5", 0.5), ("5.", 5.0), (7, 7.0)])
def test_parse_number_accepts_plain_decimals(raw, expected):
    assert parse_number(raw) == expected


# --------------------------------------------------------------------- email
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Asha@Example.COM ", "asha@example.com"),
        ("user+tag@sub.domain.co.in", "user+tag@sub.domain.co.in"),
        ("o'brien@example.ie", "o'brien@example.ie"),
    ],
)
def test_email_is_trimmed_and_lowercased(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "plainaddress",
        "a@b",
        "a..b@example.com",
        ".a@example.com",
        "a.@example.com",
        "a@-example.com",
        "a@example..com",
        "a b@example.com",
        "a@exa mple.com",
        "<script>@example.com",
        "a@example.com\nBcc: x@y.z",
        "x" * 65 + "@example.com",
        "a@" + "b" * 250 + ".com",
        None,
        123,
        {"$ne": None},
        ["a@example.com"],
    ],
)
def test_invalid_emails_are_rejected(raw):
    assert normalize_email(raw) is None


# ------------------------------------------------------------------ password
@pytest.mark.parametrize(
    "password,fragment",
    [
        ("", "required"),
        (None, "required"),
        ("short1", "at least 8"),
        ("a1" * 65, "at most 128"),
        ("onlyletters", "one letter and one number"),
        ("1234567890", "one letter and one number"),
        ("password123", "too common"),
        ("Password123", "too common"),
        ({"$ne": ""}, "required"),
    ],
)
def test_weak_passwords_are_rejected(password, fragment):
    assert fragment in password_problem(password)


def test_password_must_not_contain_email_or_name():
    assert "email" in password_problem("ashapatel2026", email="ashapatel@example.com")
    assert "name" in password_problem("xx-PATEL-99", name="Asha Patel")


def test_good_password_is_accepted():
    assert password_problem(TEST_PASSWORD, email="asha@example.test", name="Asha Patel") is None


# -------------------------------------------------------------------- signup
def _signup(**overrides):
    payload = {"name": "Asha Patel", "email": "Asha@Example.test", "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}
    payload.update(overrides)
    return payload


def test_signup_returns_clean_values():
    assert validate_signup(_signup(name="  Asha    Patel ")) == {
        "name": "Asha Patel",
        "email": "asha@example.test",
        "password": TEST_PASSWORD,
    }


def test_signup_accepts_names_in_indic_scripts():
    assert validate_signup(_signup(name="रमेश कुमार"))["name"] == "रमेश कुमार"


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"name": ""}, "name"),
        ({"name": "A"}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"name": "<script>alert(1)</script>"}, "name"),
        ({"name": "Robert'); DROP TABLE users;--"}, "name"),
        ({"name": {"$gt": ""}}, "name"),
        ({"email": "not-an-email"}, "email"),
        ({"email": {"$ne": None}}, "email"),
        ({"password": "weak"}, "password"),
        ({"confirm_password": "Different-Pass-99"}, "confirm_password"),
    ],
)
def test_signup_rejects_invalid_fields(overrides, field):
    with pytest.raises(ValidationError) as excinfo:
        validate_signup(_signup(**overrides))
    assert field in excinfo.value.details


def test_signup_ignores_legacy_government_id_fields():
    data = validate_signup(_signup(GovtId="1234-5678-9012", GovtIdType="aadhaar", Phone="9999999999"))
    assert set(data) == {"name", "email", "password"}


# --------------------------------------------------------------------- login
def test_login_normalizes_email():
    assert validate_login({"email": " ASHA@example.test", "password": "x"}) == ("asha@example.test", "x")


@pytest.mark.parametrize(
    "payload",
    [
        {"email": {"$ne": None}, "password": {"$ne": None}},
        {"email": ["a@example.test"], "password": ["x"]},
        {},
        {"email": "a@example.test", "password": ""},
        {"email": "a@example.test", "password": "x" * 129},
    ],
)
def test_login_never_passes_through_unusable_credentials(payload):
    email, password = validate_login(payload)
    assert password is None or email is None


def test_new_password_validation():
    assert validate_new_password({"password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}, email=None, name=None) == TEST_PASSWORD
    with pytest.raises(ValidationError) as excinfo:
        validate_new_password({"password": TEST_PASSWORD, "confirm_password": "nope"}, email=None, name=None)
    assert "confirm_password" in excinfo.value.details


# ---------------------------------------------------------------------- city
@pytest.mark.parametrize("city", ["Pune", "New Delhi", "St. John's", "Rio de Janeiro, BR", "पुणे", "São Paulo", "  Pune  "])
def test_valid_cities(city):
    assert validate_city(city) == " ".join(city.split())


@pytest.mark.parametrize(
    "city",
    ["", " ", "A", "x" * 81, "123", "Pune&appid=stolen", "Pune?units=imperial", "London; DROP", "<b>Pune</b>", "Pune\r\nHost: evil", None, 5, {"$gt": ""}, ["Pune"]],
)
def test_invalid_cities(city):
    with pytest.raises(ValidationError) as excinfo:
        validate_city(city)
    assert "city" in excinfo.value.details


# ----------------------------------------------------------------- object id
def test_parse_object_id_roundtrip():
    oid = ObjectId()
    assert parse_object_id(str(oid)) == oid


@pytest.mark.parametrize("raw", ["", "abc", "z" * 24, "1" * 23, "1" * 25, None, 5, {"$oid": "x"}, "../../etc/passwd"])
def test_parse_object_id_reports_bad_ids_as_not_found(raw):
    with pytest.raises(NotFoundError):
        parse_object_id(raw)


# ---------------------------------------------------------------- pagination
def test_pagination_defaults():
    assert validate_pagination({}) == (1, 10)


def test_pagination_accepts_valid_values():
    assert validate_pagination({"page": "3", "per_page": "50"}) == (3, 50)


@pytest.mark.parametrize(
    "args,field",
    [
        ({"page": "0"}, "page"),
        ({"page": "-1"}, "page"),
        ({"page": "abc"}, "page"),
        ({"page": "1.5"}, "page"),
        ({"page": "99999999"}, "page"),
        ({"per_page": "0"}, "per_page"),
        ({"per_page": "51"}, "per_page"),
        ({"per_page": "all"}, "per_page"),
    ],
)
def test_pagination_rejects_invalid_values(args, field):
    with pytest.raises(ValidationError) as excinfo:
        validate_pagination(args)
    assert field in excinfo.value.details
