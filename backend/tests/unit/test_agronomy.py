"""FAO EcoCrop limit data and the independent agronomic check."""

import json

import pytest

from app.services import agronomy_service
from app.services.agronomy_service import DATA_PATH, check


@pytest.fixture(scope="module")
def doc():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def test_every_crop_has_sane_limits(doc, crop_service):
    assert set(doc["crops"]) == set(crop_service.labels)
    for crop, entry in doc["crops"].items():
        for kind in ("temperature", "ph"):
            opt, ab = entry[f"{kind}_optimal"], entry[f"{kind}_absolute"]
            assert ab[0] <= opt[0] < opt[1] <= ab[1], (crop, kind)
        assert -10 < entry["temperature_absolute"][0] < entry["temperature_absolute"][1] < 60
        assert 0 < entry["ph_absolute"][0] < entry["ph_absolute"][1] < 14


def test_source_is_attributed(doc):
    assert "FAO EcoCrop" in doc["source"] and "Open Government Licence" in doc["source"]


def test_reference_midpoints_do_not_violate_limits(crop_midpoints):
    for crop, inputs in crop_midpoints.items():
        assert check(crop, inputs) == [], crop


def test_violations_name_the_field_and_direction():
    result = check("rice", {"temperature": 45.0, "ph": 3.0})
    assert [(v["field"], v["direction"]) for v in result] == [("temperature", "above"), ("ph", "below")]
    assert all(v["limit_low"] < v["limit_high"] for v in result)


def test_unknown_crop_and_missing_values_are_ignored():
    assert check("wheat", {"temperature": 99, "ph": 1}) == []
    assert check("rice", {"temperature": None, "ph": "x"}) == []


def test_limits_are_cached():
    agronomy_service._limits.cache_clear()
    assert agronomy_service._limits() is agronomy_service._limits()
