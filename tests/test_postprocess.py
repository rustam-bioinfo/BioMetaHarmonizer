"""Synthetic tests for optional post-processing; no network calls."""

import copy
import json

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from biometaharmonizer.postprocess import postprocess


def frame(lat_lon=None, extras=None):
    return pd.DataFrame({
        "biosample_accession": ["SAMN001"],
        "lat_lon": [lat_lon], "_extra_attributes": [extras],
        "one_health_category": ["Human"], "one_health_confidence": [0.9],
    })


@pytest.mark.parametrize("raw,expected", [
    ("35.0286 N 139.5 E", (35.0286, 139.5)),
    ("35.0286 S, 139.5 W", (-35.0286, -139.5)),
    ("35.0286, -139.5", (35.0286, -139.5)),
    ("35.0286 -139.5", (35.0286, -139.5)),
    ("0 N 0 E", (0, 0)),
    ("-90;180", (-90, 180)),
    (".5 / -.25", (0.5, -0.25)),
    ("-35 S 139 E", (-35, 139)),
])
def test_combined(raw, expected):
    result, report = postprocess(frame(raw))
    assert result.iloc[0]["decimalLatitude"] == pytest.approx(expected[0])
    assert result.iloc[0]["decimalLongitude"] == pytest.approx(expected[1])
    assert report.iloc[0]["coordinate_status"] == "parsed"


@pytest.mark.parametrize("raw,status", [
    ("91, 0", "invalid"), ("0, 181", "invalid"),
    ("-35 N 139 E", "invalid"), ("+35 S 139 E", "invalid"),
    ("35 E 139 N", "unsupported"), ("35 degrees N 139 E", "unsupported"),
    (None, "missing"), (pd.NA, "missing"), ("missing", "missing"),
    ("not collected", "missing"), ("", "missing"), ("0", "unsupported"),
])
def test_unresolved_combined(raw, status):
    result, report = postprocess(frame(raw))
    assert result[["decimalLatitude", "decimalLongitude"]].isna().all().all()
    assert report.iloc[0]["coordinate_status"] == status


@pytest.mark.parametrize("as_json", [False, True])
def test_recover_ena_and_health(as_json):
    extras = {"geographic location (latitude)": "35.0286",
              "geographic location (longitude)": "139.5", "host_health_state": "disease"}
    value = json.dumps(extras) if as_json else extras
    result, report = postprocess(frame(extras=value))
    assert result.iloc[0]["decimalLatitude"] == pytest.approx(35.0286)
    assert result.iloc[0]["decimalLongitude"] == pytest.approx(139.5)
    assert result.iloc[0]["host_health_state"] == "disease"
    assert report.iloc[0]["coordinate_status"] == "recovered"


@pytest.mark.parametrize("extras,status", [
    ({"latitude": 0, "longitude": 0}, "recovered"),
    ({"latitude": "missing", "longitude": "unknown"}, "missing"),
    ({"latitude": 35}, "incomplete"),
    ({"latitude": 91, "longitude": 0}, "invalid"),
    ({"latitude": True, "longitude": 0}, "invalid"),
    ({"latitude": [35], "longitude": 0}, "invalid"),
    ({"latitude": 35, "lat": 36, "longitude": 139}, "conflict"),
    ({"latitude": 35, "lat": "35.0", "longitude": 139}, "recovered"),
])
def test_separate_variants(extras, status):
    _, report = postprocess(frame(extras=extras))
    assert report.iloc[0]["coordinate_status"] == status


@pytest.mark.parametrize("combined,status", [("35,139", "agreement"), ("36,139", "conflict")])
def test_compare_sources(combined, status):
    result, report = postprocess(frame(combined, {"latitude": 35, "longitude": 139}))
    assert report.iloc[0]["coordinate_status"] == status
    assert pd.isna(result.iloc[0]["decimalLatitude"]) == (status == "conflict")


def test_numeric_tolerance():
    _, report = postprocess(frame("35.000000001,139", {"latitude": 35, "longitude": 139}))
    assert report.iloc[0]["coordinate_status"] == "agreement"


def test_recover_after_bad_combined():
    result, report = postprocess(frame("unsupported", {"latitude": 0, "longitude": 0}))
    assert result.iloc[0]["decimalLatitude"] == 0
    assert "lat_lon_unsupported" in report.iloc[0]["issue"]


@pytest.mark.parametrize("extras,issue", [
    ("{bad", "invalid_extra_attributes_json"),
    ("[]", "extra_attributes_not_an_object"),
    ([1], "extra_attributes_not_an_object"),
])
def test_bad_extras_does_not_lose_valid_coordinates(extras, issue):
    result, report = postprocess(frame("0,0", extras))
    assert result.iloc[0]["decimalLatitude"] == 0
    assert issue in report.iloc[0]["issue"]


def test_health_conflict_is_not_a_diagnosis():
    original = frame(extras={"host_health_state": "disease", "health status": "healthy"})
    original["host_disease"] = "original diagnosis"
    result, report = postprocess(original)
    assert pd.isna(result.iloc[0]["host_health_state"])
    assert result.iloc[0]["host_disease"] == "original diagnosis"
    assert "conflicting_host_health_state" in report.iloc[0]["issue"]


def test_preserve_original_and_duplicate_index():
    original = pd.concat([frame("0,0", {"other": [1, 2]}), frame("1,2")])
    original.index = [10, 10]
    before = copy.deepcopy(original)
    extras_before = copy.deepcopy(original.iloc[0]["_extra_attributes"])
    result, report = postprocess(original)
    assert_frame_equal(original, before)
    assert_frame_equal(result[original.columns], original)
    assert original.iloc[0]["_extra_attributes"] == extras_before
    assert report["row_position"].tolist() == [0, 1]
    assert result.index.tolist() == [10, 10]


def test_empty_and_missing_columns():
    empty, report = postprocess(pd.DataFrame())
    assert list(empty.columns) == ["decimalLatitude", "decimalLongitude", "host_health_state"]
    assert list(report.columns) == ["row_position", "biosample_accession", "coordinate_source", "coordinate_status", "issue"]
    result, report = postprocess(pd.DataFrame({"other": [1]}))
    assert len(result) == 1
    assert report.iloc[0]["coordinate_status"] == "missing"


@pytest.mark.parametrize("column", ["decimalLatitude", "decimalLongitude", "host_health_state"])
def test_existing_derived_columns_rejected(column):
    with pytest.raises(ValueError, match="already exist"):
        postprocess(pd.DataFrame({column: [1]}))


def test_bad_input_rejected():
    with pytest.raises(TypeError):
        postprocess([])
    with pytest.raises(ValueError, match="unique"):
        postprocess(pd.DataFrame([[1, 2]], columns=["lat_lon", "lat_lon"]))

