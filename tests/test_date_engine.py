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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pwr(de, value):
    """parse_with_range on a single value; returns the first result row."""
    return de.parse_with_range(pd.Series([value])).iloc[0]


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_parse_with_range_returns_dataframe(self, de):
        result = de.parse_with_range(pd.Series(["2021"]))
        assert isinstance(result, pd.DataFrame)

    def test_column_names_are_correct(self, de):
        result = de.parse_with_range(pd.Series(["2021"]))
        assert list(result.columns) == ["collection_date", "collection_date_range"]

    def test_row_count_matches_input(self, de):
        s = pd.Series(["2020", "2021-06", "missing", np.nan])
        result = de.parse_with_range(s)
        assert len(result) == 4

    def test_index_preserved_for_non_default_index(self, de):
        s = pd.Series(["2020", "2021", "missing"], index=[10, 20, 30])
        result = de.parse_with_range(s)
        assert list(result.index) == [10, 20, 30]

    def test_parse_returns_series(self, de):
        result = de.parse(pd.Series(["2020"]))
        assert isinstance(result, pd.Series)

    def test_parse_index_preserved(self, de):
        s = pd.Series(["2020", "2021"], index=[5, 15])
        result = de.parse(s)
        assert list(result.index) == [5, 15]


# ---------------------------------------------------------------------------
# Point dates
# ---------------------------------------------------------------------------

class TestPointDates:
    @pytest.mark.parametrize("raw,expected", [
        ("2020",            "2020"),
        ("2020-03",         "2020-03"),
        ("2020-3",          "2020-03"),
        ("2020-03-15",      "2020-03-15"),
        ("15 March 2020",   "2020-03-15"),
        ("March 2020",      "2020-03"),
        ("Jan/2019",        "2019-01"),
        ("2020-12-31",      "2020-12-31"),
        ("01 Jan 2000",     "2000-01-01"),
    ])
    def test_point_dates_resolved(self, de, raw, expected):
        row = _pwr(de, raw)
        assert row["collection_date"] == expected
        assert pd.isna(row["collection_date_range"])

    def test_whitespace_padded_input_parsed(self, de):
        row = _pwr(de, "  2020-03  ")
        assert row["collection_date"] == "2020-03"
        assert pd.isna(row["collection_date_range"])


# ---------------------------------------------------------------------------
# Empty / blank input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_string_returns_nan(self, de):
        row = _pwr(de, "")
        assert pd.isna(row["collection_date"])
        assert pd.isna(row["collection_date_range"])

    def test_whitespace_only_returns_nan(self, de):
        row = _pwr(de, "   ")
        assert pd.isna(row["collection_date"])
        assert pd.isna(row["collection_date_range"])


# ---------------------------------------------------------------------------
# Null patterns
# ---------------------------------------------------------------------------

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
        "not determined",
        "not recorded",
        "unavailable",
        "unspecified",
        "restricted",
        "tbd",
        "tba",
    ])
    def test_null_patterns_both_columns_nan(self, de, raw):
        row = _pwr(de, raw)
        assert pd.isna(row["collection_date"]), (
            f"collection_date should be NaN for null input {raw!r}"
        )
        assert pd.isna(row["collection_date_range"]), (
            f"collection_date_range should be NaN for null input {raw!r}"
        )

    @pytest.mark.parametrize("raw", [
        "MISSING",
        "Unknown",
        "NOT PROVIDED",
        "Not Collected",
        "NULL",
        "NA",
    ])
    def test_null_patterns_case_insensitive(self, de, raw):
        row = _pwr(de, raw)
        assert pd.isna(row["collection_date"]), (
            f"NULL_PATTERNS must be case-insensitive; {raw!r} was not matched"
        )


# ---------------------------------------------------------------------------
# NaN input
# ---------------------------------------------------------------------------

class TestNaNInput:
    def test_nan_produces_both_columns_nan(self, de):
        row = _pwr(de, np.nan)
        assert pd.isna(row["collection_date"])
        assert pd.isna(row["collection_date_range"])

    def test_series_with_multiple_nans(self, de):
        result = de.parse_with_range(pd.Series([np.nan, np.nan]))
        assert result["collection_date"].isna().all()
        assert result["collection_date_range"].isna().all()


# ---------------------------------------------------------------------------
# Two-digit year
# ---------------------------------------------------------------------------

class TestTwoDigitYearRejected:
    @pytest.mark.parametrize("raw", ["19", "99", "00", "85"])
    def test_two_digit_year_returns_nan(self, de, raw):
        row = _pwr(de, raw)
        assert pd.isna(row["collection_date"]), (
            f"Two-digit year {raw!r} must be rejected"
        )


# ---------------------------------------------------------------------------
# Range detection -- collection_date must be NaN
# ---------------------------------------------------------------------------

class TestRangeDetection:
    @pytest.mark.parametrize("raw", [
        # INSDC slash
        "2021-01-15/2021-03-20",
        "2004-07/2004-12",
        # Year-only forward range
        "2018-2020",
        "2015/2017",
        # Numeric dash / word range
        "2021-01-15 - 2021-03-20",
        "2020-06 to 2020-09",
        # Unicode en-dash and em-dash
        "2021-01-15 \u2013 2021-03-20",
        "2021-01-15 \u2014 2021-03-20",
        # Named-month same year
        "July-December 2004",
        "Jan-Mar 2019",
        # Named-month cross-year
        "Oct 2020-Feb 2021",
        "Dec 2018-Jan 2019",
        # Season strings -- all four variants
        "Spring 2019",
        "Summer 2021",
        "Autumn 2020",
        "Fall 2019",
        "Winter 2020-2021",
        # Approximate / uncertain
        "~2015",
        "circa 2010",
        "ca. 2010",
        "approx. 2015",
        "early March 2020",
        "late 2019",
        "mid-2018",
        "mid 2018",
        # Inverted year range -- still a range, collection_date must be NaN
        "2020-2018",
    ])
    def test_range_collection_date_is_nan(self, de, raw):
        row = _pwr(de, raw)
        assert pd.isna(row["collection_date"]), (
            f"Expected collection_date NaN for range input {raw!r}, "
            f"got {row['collection_date']!r}"
        )

    @pytest.mark.parametrize("raw", [
        "2018-2020",
        "Spring 2019",
        "2021-01-15/2021-03-20",
        "July-December 2004",
        "2020-2018",
        "Summer 2021",
        "2021-01-15 \u2013 2021-03-20",
    ])
    def test_range_verbatim_stored_in_range_column(self, de, raw):
        row = _pwr(de, raw)
        assert row["collection_date_range"] == raw, (
            f"Verbatim string must be preserved in collection_date_range for {raw!r}"
        )


# ---------------------------------------------------------------------------
# Same-year "range" boundary (2020-2020 is NOT a range)
# ---------------------------------------------------------------------------

class TestSameYearBoundary:
    def test_same_year_not_treated_as_range(self, de):
        """
        2020-2020 matches _YEAR_ONLY_RANGE but start == end, so _detect_range
        returns False. dateutil then parses it. The exact output depends on
        dateutil, but collection_date_range must be NaN (it is not a range).
        """
        row = _pwr(de, "2020-2020")
        assert pd.isna(row["collection_date_range"]), (
            "2020-2020 has start == end and must NOT be treated as a range"
        )


# ---------------------------------------------------------------------------
# Mixed-content series
# ---------------------------------------------------------------------------

class TestMixedSeries:
    def test_mixed_null_range_point(self, de):
        s = pd.Series(["missing", "2018-2020", "2021-06-01", np.nan])
        result = de.parse_with_range(s)

        # row 0: null
        assert pd.isna(result.iloc[0]["collection_date"])
        assert pd.isna(result.iloc[0]["collection_date_range"])
        # row 1: range
        assert pd.isna(result.iloc[1]["collection_date"])
        assert result.iloc[1]["collection_date_range"] == "2018-2020"
        # row 2: point date
        assert result.iloc[2]["collection_date"] == "2021-06-01"
        assert pd.isna(result.iloc[2]["collection_date_range"])
        # row 3: NaN
        assert pd.isna(result.iloc[3]["collection_date"])
        assert pd.isna(result.iloc[3]["collection_date_range"])


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_repeated_value_parsed_correctly(self, de):
        s = pd.Series(["2021-06", "2021-06", "2021-06"])
        result = de.parse_with_range(s)
        assert list(result["collection_date"]) == ["2021-06", "2021-06", "2021-06"]

    def test_repeated_range_preserved_correctly(self, de):
        s = pd.Series(["2018-2020", "2018-2020"])
        result = de.parse_with_range(s)
        assert list(result["collection_date_range"]) == ["2018-2020", "2018-2020"]
        assert result["collection_date"].isna().all()


# ---------------------------------------------------------------------------
# DataFrame input passthrough
# ---------------------------------------------------------------------------

class TestDataFrameInput:
    def test_parse_with_range_accepts_dataframe(self, de):
        df = pd.DataFrame({"col": ["2020", "missing", "2018-2020"]})
        result = de.parse_with_range(df)
        assert isinstance(result, pd.DataFrame)
        assert result.iloc[0]["collection_date"] == "2020"
        assert pd.isna(result.iloc[1]["collection_date"])
        assert pd.isna(result.iloc[2]["collection_date"])

    def test_parse_accepts_dataframe(self, de):
        df = pd.DataFrame({"col": ["2021-03", "unknown"]})
        result = de.parse(df)
        assert isinstance(result, pd.Series)
        assert result.iloc[0] == "2021-03"
        assert pd.isna(result.iloc[1])


# ---------------------------------------------------------------------------
# parse() / parse_with_range() mutual consistency
# ---------------------------------------------------------------------------

class TestParseConsistency:
    def test_parse_matches_parse_with_range_collection_date(self, de):
        s = pd.Series(["2020", "2020-06", "Spring 2019", "missing", np.nan, "2021-01-15"])
        from_parse = de.parse(s)
        from_pwr   = de.parse_with_range(s)["collection_date"]
        # Compare element-wise, treating NaN == NaN as equal
        for i in range(len(s)):
            a, b = from_parse.iloc[i], from_pwr.iloc[i]
            both_nan = (isinstance(a, float) and np.isnan(a)) and \
                       (isinstance(b, float) and np.isnan(b))
            assert both_nan or a == b, (
                f"Mismatch at index {i}: parse()={a!r} vs parse_with_range()={b!r}"
            )


# ---------------------------------------------------------------------------
# Internal methods
# ---------------------------------------------------------------------------

class TestInternalMethods:
    def test_parse_single_returns_string_for_point_date(self, de):
        assert de._parse_single("2021-06") == "2021-06"

    def test_parse_single_returns_nan_for_null(self, de):
        assert pd.isna(de._parse_single("missing"))

    def test_parse_single_with_range_point_date_structure(self, de):
        row = de._parse_single_with_range("2021-06-15")
        assert row["collection_date"] == "2021-06-15"
        assert pd.isna(row["collection_date_range"])

    def test_parse_single_with_range_range_structure(self, de):
        row = de._parse_single_with_range("2018-2020")
        assert pd.isna(row["collection_date"])
        assert row["collection_date_range"] == "2018-2020"

    def test_empty_row_has_both_nan(self):
        row = DateEngine._empty_row()
        assert pd.isna(row["collection_date"])
        assert pd.isna(row["collection_date_range"])

    def test_detect_range_true_for_insdc_slash(self):
        assert DateEngine._detect_range("2021-01/2021-06") is True

    def test_detect_range_false_for_point_date(self):
        assert DateEngine._detect_range("2021-06-15") is False


# ---------------------------------------------------------------------------
# parse() API
# ---------------------------------------------------------------------------

class TestParseMethod:
    def test_parse_returns_series(self, de):
        s = pd.Series(["2020", "2020-05", "missing"])
        result = de.parse(s)
        assert isinstance(result, pd.Series)
        assert result.iloc[0] == "2020"
        assert result.iloc[1] == "2020-05"
        assert pd.isna(result.iloc[2])

    def test_parse_range_input_returns_nan(self, de):
        result = de.parse(pd.Series(["2018-2020"]))
        assert pd.isna(result.iloc[0])

    def test_parse_all_nulls_returns_all_nan(self, de):
        s = pd.Series(["missing", np.nan, "unknown"])
        result = de.parse(s)
        assert result.isna().all()
