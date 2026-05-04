import logging
import re

import numpy as np
import pandas as pd
from dateutil import parser as dateutil_parser


logger = logging.getLogger(__name__)


class DateEngine:
    """
    Module 3: Temporal Parsing Engine.
    Converts any date format to ISO 8601 truncated representation:
      - Year-only: "YYYY"
      - Year-month: "YYYY-MM"
      - Full date: "YYYY-MM-DD"

    Range contract
    --------------
    collection_date is a POINT-DATE field and is ALWAYS NaN for any range
    or approximate input, without exception.  collection_date_range receives
    the verbatim original string for all range inputs.

    Supported range formats detected before dateutil:
      - Numeric INSDC slash:       2004-07/2004-12, 2021-01-15/2021-03-20
      - Year-only range:           2018-2020, 2015/2017
      - Numeric dash/word range:   2021-01-15 - 2021-03-20, 2020-06 to 2020-09
      - Named-month same year:     July-December 2004, Jan-Mar 2019
      - Named-month cross-year:    Oct 2020-Feb 2021, Dec 2018-Jan 2019
      - Season strings:            Spring 2019, Winter 2020-2021
      - Approximate/uncertain:     ~2015, circa 2010, early March 2020, late 2019
    """

    # Canonical null pattern set -- kept in sync with ingestion._NULL_PATTERNS
    # so that DateEngine used directly on raw (un-ingested) data handles the
    # same null variants.  (MED-3 / issue #54)
    NULL_PATTERNS = re.compile(
        r"^(?:-+|\.+|n/?a|na|nd|nr|ns|nt|none|null|nil|"
        r"missing|misssing|missng|mising|"
        r"unknown|unkown|unknwon|unknow|"
        r"not\s+provided|not\s+collected|not\s+applicable|not\s+available|"
        r"not\s+determined|not\s+recorded|not\s+reported|not\s+known|"
        r"not\s+given|not\s+stated|not\s+specified|"
        r"not\s+done|not\s+tested|not\s+sequenced|not\s+typed|"
        r"unavailable|unspecified|undetermined|unidentified|"
        r"restricted|restricted\s+access|withheld|confidential|"
        r"tbd|tba|"
        r"missing\s*:.*|not\s+applicable\s*:.*|data\s+agreement\s+established\s+pre-?2023)$",
        re.IGNORECASE,
    )
    YEAR_ONLY  = re.compile(r"^(\d{4})$")
    YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$|^([A-Za-z]{3,9})[-/\s](\d{4})$")
    TWO_DIGIT_YEAR = re.compile(r"^\d{2}$")

    _INSDC_SLASH_RANGE = re.compile(
        r"^\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?\s*/\s*\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?$"
    )

    _YEAR_ONLY_RANGE = re.compile(
        r"^(?P<start>\d{4})\s*[-/]\s*(?P<end>\d{4})$"
    )

    _NUMERIC_DASH_RANGE = re.compile(
        r"^\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?"
        r"(?:\s*[\-\u2013\u2014]\s*|\s+to\s+)"
        r"\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?$",
        re.IGNORECASE,
    )

    _NAMED_MONTH_SAME_YEAR = re.compile(
        r"^[A-Za-z]{3,9}\s*[-/]\s*[A-Za-z]{3,9}\s+\d{4}$"
    )

    _NAMED_MONTH_CROSS_YEAR = re.compile(
        r"^[A-Za-z]{3,9}\s+\d{4}\s*[-/]\s*[A-Za-z]{3,9}\s+\d{4}$"
    )

    _SEASON_RANGE = re.compile(
        r"^(?:spring|summer|autumn|fall|winter)\s+\d{4}(?:\s*[-/]\s*\d{4})?$",
        re.IGNORECASE,
    )

    _APPROX_DATE = re.compile(
        r"^(?:~|circa\s|ca\.?\s|approx\.?\s|late\s|early\s|mid[-\s])\S",
        re.IGNORECASE,
    )

    @staticmethod
    def _empty_row():
        return {"collection_date": np.nan, "collection_date_range": np.nan}

    @staticmethod
    def _detect_range(value):
        v = value.strip()
        if DateEngine._INSDC_SLASH_RANGE.match(v):
            return True
        m = DateEngine._YEAR_ONLY_RANGE.match(v)
        if m:
            start, end = int(m.group("start")), int(m.group("end"))
            if start != end:
                if start > end:
                    logger.warning(
                        "Inverted year range detected: %r (start=%d > end=%d). "
                        "Preserving verbatim in collection_date_range.",
                        v, start, end,
                    )
                return True
        if DateEngine._NUMERIC_DASH_RANGE.match(v):
            return True
        if DateEngine._NAMED_MONTH_SAME_YEAR.match(v):
            return True
        if DateEngine._NAMED_MONTH_CROSS_YEAR.match(v):
            return True
        if DateEngine._SEASON_RANGE.match(v):
            return True
        if DateEngine._APPROX_DATE.match(v):
            return True
        return False

    def parse(self, series):
        """
        Parse a Series of date strings to ISO 8601 truncated point dates.
        Deduplicates unique values before parsing for performance.
        """
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        unique_vals = series.dropna().unique()
        cache = {v: self._parse_single(v) for v in unique_vals}
        return series.map(lambda v: cache.get(v, np.nan))

    def parse_with_range(self, series):
        """
        Parse dates and return a DataFrame with columns:
          - collection_date:       ISO 8601 point date, or NaN for any range input
          - collection_date_range: verbatim original string for range inputs, else NaN

        Deduplicates unique values before parsing for performance.
        """
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        unique_vals = series.dropna().unique()
        cache = {v: self._parse_single_with_range(v) for v in unique_vals}
        results = series.map(lambda v: cache.get(v) if pd.notna(v) else self._empty_row())
        return pd.DataFrame(results.tolist(), index=series.index)

    def _parse_single(self, value):
        return self._parse_single_with_range(value)["collection_date"]

    def _parse_single_with_range(self, value):
        if pd.isna(value):
            return self._empty_row()

        value = str(value).strip()
        if not value:
            return self._empty_row()
        if self.NULL_PATTERNS.match(value):
            return self._empty_row()

        if self._detect_range(value):
            return {
                "collection_date": np.nan,
                "collection_date_range": value,
            }

        if self.TWO_DIGIT_YEAR.match(value):
            logger.warning("Rejecting two-digit year string: '%s'", value)
            return self._empty_row()

        parsed = self._parse_date_string(value)
        return {"collection_date": parsed, "collection_date_range": np.nan}

    def _parse_date_string(self, value):
        if self.YEAR_ONLY.match(value):
            return value
        if self.YEAR_MONTH.match(value):
            return self._resolve_year_month(value)
        try:
            parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            logger.warning("Could not parse date string: '%s'", value)
            return np.nan

    def _resolve_year_month(self, value):
        try:
            parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m")
        except (ValueError, OverflowError):
            logger.warning("Could not parse year-month string: '%s'", value)
            return np.nan
