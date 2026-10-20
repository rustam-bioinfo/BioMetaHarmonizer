import logging
import re
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from dateutil import parser as dateutil_parser
from dateutil.parser import UnknownTimezoneWarning


logger = logging.getLogger(__name__)


class DateEngine:
    """
    Module 2: Temporal Parsing Engine.
    Normalize collection dates and supported timestamps to ISO 8601:
      - Year-only: YYYY
      - Year-month: YYYY-MM
      - Full date: YYYY-MM-DD
      - Explicit clock time: preserve supplied hour/minute/second precision
      - Fractional seconds and resolved timezone offsets: preserve when supplied

    collection_date contains a point date or timestamp. Ranges and approximate
    inputs receive NaN there, with their original stripped string retained in
    collection_date_range. Supported ISO-style timestamp intervals use a slash
    or a spaced dash/to separator. Existing date-range patterns are retained.

    Timestamps require an explicit complete calendar date. Missing times and
    timezones are not inferred. Unknown timezone abbreviations are rejected
    with a warning rather than silently dropping timezone information.
    Same-year degenerate ranges such as YYYY-YYYY collapse to one point year.
    """

    # Keep null handling consistent with ingestion._NULL_PATTERNS.
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
    YEAR_ONLY = re.compile(r"^(\d{4})$")
    YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$|^([A-Za-z]{3,9})[-/\s](\d{4})$")
    TWO_DIGIT_YEAR = re.compile(r"^\d{2}$")

    _CLOCK_TIME = re.compile(
        r"(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})"
        r"(?::(?P<second>\d{2})(?P<fraction>[.,]\d+)?)?"
        r"(?:\s*(?:a\.?m\.?|p\.?m\.?))?(?![\d:.])",
        re.IGNORECASE,
    )
    _AMPM_TIME = re.compile(
        r"(?<!\d)(?P<hour>\d{1,2})\s*(?:a\.?m\.?|p\.?m\.?)(?!\w)",
        re.IGNORECASE,
    )
    _ISO_TIME_HINT = re.compile(r"[Tt]\d{2}")
    _TRAILING_ZONE = re.compile(r"(?P<zone>[Zz]|[+-]\d{2}:?\d{2})\s*$")

    _ISO_ENDPOINT = (
        r"\d{4}(?:-\d{2}(?:-\d{2}"
        r"(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?"
        r"(?:[Zz]|[+-]\d{2}:?\d{2})?)?)?)?"
    )
    _ISO_TIMESTAMP_RANGE = re.compile(
        rf"^{_ISO_ENDPOINT}(?:\s*/\s*|\s+(?:to|[-\u2013\u2014])\s+){_ISO_ENDPOINT}$",
        re.IGNORECASE,
    )

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
    _SAME_YEAR = object()

    @staticmethod
    def _empty_row():
        return {"collection_date": np.nan, "collection_date_range": np.nan}

    @staticmethod
    def _detect_range(value):
        """Return True for ranges, False for points, or _SAME_YEAR."""
        v = value.strip()
        if DateEngine._INSDC_SLASH_RANGE.match(v):
            return True
        m = DateEngine._YEAR_ONLY_RANGE.match(v)
        if m:
            start, end = int(m.group("start")), int(m.group("end"))
            if start == end:
                return DateEngine._SAME_YEAR
            if start > end:
                logger.warning(
                    "Inverted year range detected: %r (start=%d > end=%d). "
                    "Preserving verbatim in collection_date_range.",
                    v, start, end,
                )
            return True
        if DateEngine._ISO_TIMESTAMP_RANGE.match(v):
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
        """Normalize point dates/timestamps, parsing unique values once."""
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        unique_vals = series.dropna().unique()
        cache = {v: self._parse_single(v) for v in unique_vals}
        return series.map(lambda v: cache.get(v, np.nan))

    def parse_with_range(self, series):
        """Return point dates/timestamps and separate verbatim range strings."""
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
        if not value or self.NULL_PATTERNS.match(value):
            return self._empty_row()

        range_result = self._detect_range(value)
        if range_result is self._SAME_YEAR:
            year = value.strip().split("-")[0].strip()
            return {"collection_date": year, "collection_date_range": np.nan}
        if range_result:
            return {"collection_date": np.nan, "collection_date_range": value}
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
            time_match = self._CLOCK_TIME.search(value) or self._AMPM_TIME.search(value)
            with warnings.catch_warnings():
                warnings.simplefilter("error", UnknownTimezoneWarning)
                if time_match:
                    parsed = dateutil_parser.parse(
                        value, dayfirst=False, default=datetime(2000, 1, 1)
                    )
                    alternate = dateutil_parser.parse(
                        value, dayfirst=False, default=datetime(2004, 2, 2)
                    )
                    if parsed.date() != alternate.date():
                        raise ValueError("Timestamp requires an explicit complete calendar date")
                    return self._format_timestamp(parsed, time_match, value)
                if self._ISO_TIME_HINT.search(value):
                    raise ValueError("Unsupported timestamp clock format")
                parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, OverflowError, UnknownTimezoneWarning) as exc:
            logger.warning("Could not parse date string %r: %s", value, exc)
            return np.nan

    @staticmethod
    def _format_timestamp(parsed, time_match, original):
        components = time_match.groupdict()
        result = f"{parsed.date().isoformat()}T{parsed.hour:02d}"
        if components.get("minute") is not None:
            result += f":{parsed.minute:02d}"
        if components.get("second") is not None:
            result += f":{parsed.second:02d}"
            fraction = components.get("fraction")
            if fraction:
                result += "." + fraction[1:]

        offset = parsed.utcoffset()
        if offset is not None:
            offset_seconds = int(offset.total_seconds())
            if offset_seconds % 60:
                raise ValueError("Timezone offsets with seconds are unsupported")
            zone_match = DateEngine._TRAILING_ZONE.search(original)
            zone = zone_match.group("zone") if zone_match else None
            if offset_seconds == 0 and zone and zone.upper() == "Z":
                result += "Z"
            elif offset_seconds == 0 and zone in ("-0000", "-00:00"):
                result += "-00:00"
            else:
                sign = "+" if offset_seconds >= 0 else "-"
                hours, minutes = divmod(abs(offset_seconds) // 60, 60)
                result += f"{sign}{hours:02d}:{minutes:02d}"
        return result

    def _resolve_year_month(self, value):
        try:
            parsed = dateutil_parser.parse(value, dayfirst=False)
            return parsed.strftime("%Y-%m")
        except (ValueError, OverflowError):
            logger.warning("Could not parse year-month string: '%s'", value)
            return np.nan
