import logging
import re

import numpy as np
import pandas as pd
import pycountry
from rapidfuzz import fuzz


logger = logging.getLogger(__name__)


class GeoEngine:
    """
    Module 4: Geospatial Resolution Engine.
    Parses NCBI-style geo_loc_name strings into
    separate geo_country, geo_region, and geo_locality columns.
    Expected input format: 'Country: Region, Locality'
    Fallback format (no colon): 'Country, Locality'

    Output columns
    --------------
    geo_country   : raw country token from the input string (str or NaN)
                    Exception: UK sub-countries are normalised to
                    "United Kingdom".
                    NaN is returned when the token cannot be resolved to a
                    known or historical country (e.g. free text, water bodies,
                    unrecognised strings).
    geo_region    : sub-national region as submitted (str or NaN)
    geo_locality  : locality / sub-region as submitted (str or NaN)
    geo_iso3166   : ISO 3166-1 alpha-2 country code (str or 'HISTORICAL' or NaN)
    geo_sea_ocean : any named water body -- ocean, sea, gulf, bay, strait,
                    fjord, lake, reservoir, etc. (str or NaN).  The column
                    name is kept for historical compatibility; it covers all
                    aquatic geographic features, not only marine ones.

    Water body detection (two-tier)
    --------------------------------
    Tier 1 -- explicit set _OCEAN_SEA: exact case-insensitive lookup for
              canonical names (zero false positives).
    Tier 2 -- regex _WATER_BODY_RE: catches any token containing a water-body
              keyword (ocean, sea, gulf, bay, strait, fjord, bight, sound,
              inlet, lagoon, lake, reservoir, estuary, delta, reef, atoll)
              that is NOT followed by "islands?", "territory", or "states?".
              Applied only when Tier 1 misses.

    Strings that cannot be parsed (unrecognised country names, free-text
    descriptions, etc.) return all five columns as NaN.  Coordinate data
    belongs in the lat_lon attribute, not geo_loc_name; strings that happen
    to look like coordinates are treated as unparseable and return all-NaN.
    """

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

    # Strips ONE trailing parenthetical group per application.
    # Applied in a loop in _strip_parens() to remove all trailing groups.
    _PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")

    # Tier-2 water body keyword regex.
    # Negative lookahead blocks known false positives:
    #   "Channel Islands"  -- channel + islands
    #   "Pacific Islands"  -- not triggered (no keyword match)
    #   "Gulf States"      -- gulf + states
    #   "Island Territories" -- no keyword match
    # "lake" and "reservoir" are included deliberately; geo_sea_ocean covers
    # all named aquatic features (see column docstring above).
    _WATER_BODY_RE = re.compile(
        r"\b(ocean|sea|gulf|bay|strait|fjord|bight|sound|inlet|"
        r"lagoon|lake|reservoir|estuary|delta|reef|atoll)\b"
        r"(?!\s*(islands?|territory|states?))",
        re.IGNORECASE,
    )

    # Minimum rapidfuzz token_sort_ratio score accepted for fuzzy country
    # matches.  Scores below this reject the match and return NaN (prevents
    # free-text strings like "Clinical lab sample" from matching "Samoa").
    # Note: this threshold is only reached when the input does NOT directly
    # match alpha_2, alpha_3, or common_name -- those are accepted first.
    _FUZZY_MIN_SCORE = 80

    _UK_SUBCOUNTRY = {
        "england": "GB",
        "scotland": "GB",
        "wales": "GB",
        "northern ireland": "GB",
    }

    _COUNTRY_ALIASES = {
        "turkey": "TR",
        "t\u00fcrkiye": "TR",
        "namibia": "NA",
        "democratic republic of the congo": "CD",
        "dr congo": "CD",
        "drc": "CD",
        "congo-kinshasa": "CD",
        "burma": "MM",
        "myanmar (burma)": "MM",
        "palestine": "PS",
        "palestinian territories": "PS",
        "palestinian territory": "PS",
        "occupied palestinian territory": "PS",
        "gaza strip": "PS",
        "gaza": "PS",
        "west bank": "PS",
    }

    _HISTORICAL_COUNTRIES = {
        "ussr",
        "soviet union",
        "union of soviet socialist republics",
        "yugoslavia",
        "sfr yugoslavia",
        "socialist federal republic of yugoslavia",
        "czechoslovakia",
        "cssr",
        "german democratic republic",
        "east germany",
        "west germany",
        "federal republic of germany",
        "north vietnam",
        "south vietnam",
        "north yemen",
        "south yemen",
        "zaire",
        "byelorussia",
        "byelorussian ssr",
        "serbia and montenegro",
        "netherlands antilles",
    }

    # MED-5: Explicit set for canonical water body names (Tier 1).
    # Tier 2 (_WATER_BODY_RE) catches anything not listed here.
    _OCEAN_SEA = {
        # Oceans
        "pacific ocean", "atlantic ocean", "indian ocean",
        "arctic ocean", "southern ocean",
        # Major seas
        "mediterranean sea", "north sea", "caribbean sea",
        "south china sea", "east china sea", "yellow sea",
        "baltic sea", "red sea", "black sea", "caspian sea",
        "arabian sea", "coral sea", "tasman sea",
        "bering sea", "barents sea", "norwegian sea",
        "labrador sea", "andaman sea", "laccadive sea",
        "banda sea", "celebes sea", "java sea", "timor sea",
        "sulu sea", "philippine sea", "sea of japan",
        "sea of okhotsk", "sea of azov", "ross sea",
        "weddell sea", "scotia sea",
        # Arctic and marginal seas
        "kara sea", "laptev sea", "east siberian sea",
        "chukchi sea", "beaufort sea", "white sea",
        "greenland sea", "irminger sea",
        "mawson sea", "amundsen sea",
        "davis strait",
        # Gulfs and bays
        "persian gulf", "gulf of mexico", "gulf of aden",
        "gulf of guinea", "gulf of oman", "gulf of california",
        "gulf of thailand", "gulf of tonkin",
        "bay of bengal", "bay of biscay", "hudson bay",
        # Straits and channels
        "english channel", "mozambique channel",
        "strait of malacca",
    }

    # Column order guaranteed on every output DataFrame, including empty input.
    _COLUMNS = ["geo_country", "geo_region", "geo_locality", "geo_iso3166", "geo_sea_ocean"]

    def _empty(self):
        return {
            "geo_country": np.nan,
            "geo_region": np.nan,
            "geo_locality": np.nan,
            "geo_iso3166": np.nan,
            "geo_sea_ocean": np.nan,
        }

    def _strip_parens(self, s):
        """Strip all trailing parenthetical groups from s."""
        while True:
            stripped = self._PAREN_RE.sub("", s).strip()
            if stripped == s:
                return s
            s = stripped

    def _water_body_result(self, country_str_clean, region_str, locality_str):
        """Build result dict for a detected water body token."""
        result = self._empty()
        result["geo_sea_ocean"] = country_str_clean
        if region_str:
            result["geo_locality"] = (
                region_str if not locality_str else f"{region_str}, {locality_str}"
            )
        return result

    def parse(self, series):
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]

        empty = self._empty()

        unique_vals = series.dropna().unique()
        logger.debug(
            "GeoEngine: %d rows, %d unique non-null values to resolve.",
            len(series), len(unique_vals),
        )

        cache = {v: self._parse_single(v) for v in unique_vals}

        results = [
            cache[v] if pd.notna(v) and v in cache else empty
            for v in series
        ]
        return pd.DataFrame(results, index=series.index, columns=self._COLUMNS)

    def _parse_single(self, value):
        empty = self._empty()
        if pd.isna(value):
            return empty

        value = str(value).strip()
        if self.NULL_PATTERNS.match(value):
            return empty

        country_str, region_str, locality_str = self._split_geo_string(value)

        country_str_clean = self._strip_parens(country_str)
        lower_country = country_str_clean.lower()

        # Tier 1: explicit canonical water body set
        if lower_country in self._OCEAN_SEA:
            return self._water_body_result(country_str_clean, region_str, locality_str)

        # Tier 2: regex fallback for any water body keyword not in the set
        if self._WATER_BODY_RE.search(country_str_clean):
            logger.debug(
                "GeoEngine: %r matched water body regex (Tier 2) -> geo_sea_ocean.",
                country_str_clean,
            )
            return self._water_body_result(country_str_clean, region_str, locality_str)

        if lower_country in self._UK_SUBCOUNTRY:
            display_country = "United Kingdom"
        else:
            display_country = country_str_clean

        iso_code = self._resolve_iso(country_str_clean)

        # If ISO resolution completely failed and the token is not a known
        # historical entry, the country string is unrecognisable (free text,
        # coordinate fragment, etc.) -- return all-NaN.
        if pd.isna(iso_code) and lower_country not in self._HISTORICAL_COUNTRIES:
            logger.debug(
                "GeoEngine: unresolvable country token %r -- returning all-NaN.",
                country_str_clean,
            )
            return empty

        return {
            "geo_country": display_country if country_str_clean else np.nan,
            "geo_region": region_str if region_str else np.nan,
            "geo_locality": locality_str if locality_str else np.nan,
            "geo_iso3166": iso_code,
            "geo_sea_ocean": np.nan,
        }

    def _split_geo_string(self, value):
        country_str = region_str = locality_str = ""
        if ":" in value:
            parts = value.split(":", 1)
            country_str = parts[0].strip()
            remainder = parts[1].strip()
            if "," in remainder:
                sub = remainder.split(",", 1)
                region_str = sub[0].strip()
                locality_str = sub[1].strip()
            else:
                region_str = remainder
        else:
            if "," in value and not re.search(r"\([^)]*,[^)]*\)", value):
                parts = value.split(",", 1)
                country_str = parts[0].strip()
                locality_str = parts[1].strip()
            else:
                country_str = value
        return country_str, region_str, locality_str

    def _resolve_iso(self, country_str):
        if not country_str:
            return np.nan

        lower = country_str.lower().strip()

        if lower in self._UK_SUBCOUNTRY:
            return self._UK_SUBCOUNTRY[lower]

        if lower == "korea":
            logger.info(
                "'Korea' without qualifier resolved to South Korea (KR). "
                "Use 'North Korea' for DPRK."
            )
            return "KR"

        if lower in ("taiwan", "taiwan, province of china"):
            return "TW"

        if lower in self._COUNTRY_ALIASES:
            return self._COUNTRY_ALIASES[lower]

        if lower in self._HISTORICAL_COUNTRIES:
            logger.warning(
                "Historical/defunct country name '%s' detected -- tagging geo_iso3166 as HISTORICAL.",
                country_str,
            )
            return "HISTORICAL"

        if len(lower) < 3:
            logger.warning(
                "Country string too short for reliable ISO lookup: '%s'", country_str
            )
            return np.nan

        try:
            matches = pycountry.countries.search_fuzzy(country_str)
            if not matches:
                return np.nan
            best = matches[0]
            # Accept directly if the input matches alpha-2, alpha-3, or
            # common_name -- these bypass the score gate because short ISO
            # codes and common short-form names (e.g. 'USA', 'Russia',
            # 'Iran') score poorly against pycountry's long diplomatic names
            # ('United States', 'Russian Federation', 'Iran, Islamic Republic
            # of') even though the match is unambiguous.
            direct_matches = {best.alpha_2.lower(), best.alpha_3.lower()}
            common = getattr(best, "common_name", None)
            if common:
                direct_matches.add(common.lower())
            if lower in direct_matches:
                return best.alpha_2
            score = fuzz.token_sort_ratio(lower, best.name.lower())
            if score < self._FUZZY_MIN_SCORE:
                logger.debug(
                    "GeoEngine: fuzzy match '%s' -> '%s' rejected (score %d < %d).",
                    country_str, best.name, score, self._FUZZY_MIN_SCORE,
                )
                return np.nan
            return best.alpha_2
        except Exception:
            return np.nan
