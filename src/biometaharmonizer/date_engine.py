import logging
import pandas as pd
import numpy as np
from dateutil import parser as dateutil_parser
import re


logger = logging.getLogger(__name__)


class DateEngine:
    """
    Module 3: Temporal Parsing Engine.
    Converts any date format to ISO 8601 truncated representation:
      - Year-only: "YYYY"
      - Year-month: "YYYY-MM"
      - Full date: "YYYY-MM-DD"
    Handles partial dates, INSDC date ranges, and null-like strings.
    """

    NULL_PATTERNS = re.compile(
        r"^("
        r"missing|unknown|n/?a|not provided|not collected|na|none|--"
        r"|missing:\s*.*"
        r"|not applicable:\s*.*"
        r"|not applicable"
        r"|restricted access"
        r")$",
        re.IGNORECASE,
    )
    YEAR_ONLY = re.compile(r"^(\d{4})$")
    YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$|^([A-Za-z]{3,9})[-/\s](\d{4})$")
    TWO_DIGIT_YEAR = re.compile(r"^\d{2}$")
    INSDC_RANGE = re.compile(r"^(\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?)\s*/\s*(\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?)$")

    def parse(self, series):
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        return series.apply(self._parse_single)

    def parse_with_range(self, series):
        """
        Parse dates and return a DataFrame with columns:
          - collection_date: parsed ISO 8601 truncated date (start date for ranges)
          - collection_date_range: the full range string if input was a range, else NaN
        """
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        results = series.apply(self._parse_single_with_range)
        return pd.DataFrame(results.tolist(), index=series.index)

    def _parse_single(self, value):
        result = self._parse_single_with_range(value)
        return result["collection_date"]

    def _parse_single_with_range(self, value):
        empty = {"collection_date": np.nan, "collection_date_range": np.nan}
        if not isinstance(value, str) and pd.isna(value):
            return empty
        value = str(value).strip()
        if not value:
            return empty
        if self.NULL_PATTERNS.match(value):
            return empty

        # Two-digit year guard
        if self.TWO_DIGIT_YEAR.match(value):
            logger.warning("Rejecting two-digit year string: '%s'", value)
            return empty

        # INSDC date range: "2019/2020" or "2019-01/2020-03"
        range_match = self.INSDC_RANGE.match(value)
        if range_match:
            start_str = range_match.group(1)
            parsed_start = self._parse_date_string(start_str)
            return {
                "collection_date": parsed_start,
                "collection_date_range": value,
            }

        parsed = self._parse_date_string(value)
        return {"collection_date": parsed, "collection_date_range": np.nan}

    def _parse_date_string(self, value):
        """Parse a single date string (not a range) into ISO 8601 truncated form."""
        if self.YEAR_ONLY.match(value):
            return value
        if self.YEAR_MONTH.match(value):
            return self._resolve_year_month(value)
        try:
            parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return np.nan

    def _resolve_year_month(self, value):
        try:
            parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m")
        except (ValueError, OverflowError):
            return np.nan
