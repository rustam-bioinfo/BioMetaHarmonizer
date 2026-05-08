"""
Tests for date_engine.py.

All tests use synthetic data -- no live NCBI calls are made.
"""

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.date_engine import DateEngine


@pytest.fixture(scope="module")
def de():
    return DateEngine()


class TestPointDates:
    @pytest.mark.parametrize("raw,expected", [
        ("2020",       "2020"),
        ("2020-03",    "2020-03"),
        ("2020-3",     "2020-03"),
        ("2020-03-15", "2020-03-15"),
        ("15 March 2020", "2020-03-15"),
        ("March 2020",    "2020-03"),
        ("Jan/2019",      "2019-01"),
    ])
    def test_point_dates_resolved(self, de, raw, expected):
        result = de.parse_with_range(pd.Series([raw]))
        assert result.iloc[0]["collection_date"] == expected
        assert pd.isna(result.iloc[0]["collection_date_range"])


class TestNullValues:
    @pytest.mark.parametrize("raw", [
        "missing",
        "unknown",
        "not provided",
        "not collected",
        "NA",
        "N/A",
        "na",
        "none",
        "null",
        "---",
        "nd",
        "not applicable",
        "not available",
    ])
    def test_null_patterns_return_nan(self, de, raw):
        result = de.parse_with_range(pd.Series([raw]))
        assert pd.isna(result.iloc[0]["collection_date"])
        assert pd.isna(result.iloc[0]["collection_date_range"])


class TestRangeDetection:
    @pytest.mark.parametrize("raw", [
        "2018-2020",
        "2015/2017",
        "2021-01-15/2021-03-20",
        "2004-07/2004-12",
        "2021-01-15 - 2021-03-20",
        "2020-06 to 2020-09",
        "July-December 2004",
        "Jan-Mar 2019",
        "Oct 2020-Feb 2021",
        "Spring 2019",
        "Winter 2020-2021",
        "~2015",
        "circa 2010",
        "early March 2020",
        "late 2019",
        "mid-2018",
    ])
    def test_range_collection_date_is_nan(self, de, raw):
        result = de.parse_with_range(pd.Series([raw]))
        assert pd.isna(result.iloc[0]["collection_date"]), (
            f"Expected collection_date NaN for range input {raw!r}, "
            f"got {result.iloc[0]['collection_date']!r}"
        )

    @pytest.mark.parametrize("raw", [
        "2018-2020",
        "Spring 2019",
        "2021-01-15/2021-03-20",
        "July-December 2004",
    ])
    def test_range_verbatim_stored_in_range_column(self, de, raw):
        result = de.parse_with_range(pd.Series([raw]))
        assert result.iloc[0]["collection_date_range"] == raw


class TestTwoDigitYearRejected:
    def test_two_digit_year_returns_nan(self, de):
        result = de.parse_with_range(pd.Series(["19"]))
        assert pd.isna(result.iloc[0]["collection_date"])


class TestNaNInput:
    def test_nan_row_produces_nan_output(self, de):
        result = de.parse_with_range(pd.Series([np.nan]))
        assert pd.isna(result.iloc[0]["collection_date"])
        assert pd.isna(result.iloc[0]["collection_date_range"])


class TestDeduplication:
    def test_repeated_value_parsed_once_correctly(self, de):
        series = pd.Series(["2021-06", "2021-06", "2021-06"])
        result = de.parse_with_range(series)
        assert list(result["collection_date"]) == ["2021-06", "2021-06", "2021-06"]


class TestParseMethod:
    def test_parse_returns_series(self, de):
        s = pd.Series(["2020", "2020-05", "missing"])
        result = de.parse(s)
        assert isinstance(result, pd.Series)
        assert result.iloc[0] == "2020"
        assert result.iloc[1] == "2020-05"
        assert pd.isna(result.iloc[2])
