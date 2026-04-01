import pandas as pd
import numpy as np
from dateutil import parser as dateutil_parser
import re


class DateEngine:
    """
    Module 3: Temporal Parsing Engine.
    Converts any date format to ISO 8601 (YYYY-MM-DD).
    Handles partial dates, ranges, and null-like strings.
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|na|none|--)$",
        re.IGNORECASE
    )
    YEAR_ONLY = re.compile(r"^(\d{4})$")
    YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$|^([A-Za-z]{3,9})[-/\s](\d{4})$")

    def parse(self, series):
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        return series.apply(self._parse_single)

    def _parse_single(self, value):
        if not isinstance(value, str) and pd.isna(value):
            return np.nan
        value = str(value).strip()
        if not value:
            return np.nan
        if self.NULL_PATTERNS.match(value):
            return np.nan
        if self.YEAR_ONLY.match(value):
            return f"{value}-XX-XX"
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
            return parsed.strftime("%Y-%m-XX")
        except (ValueError, OverflowError):
            return np.nan
