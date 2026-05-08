"""
Tests for geo_engine.py.

All tests use synthetic data -- no live NCBI calls are made.
"""

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.geo_engine import GeoEngine


@pytest.fixture(scope="module")
def ge():
    return GeoEngine()


def _parse(ge, value):
    """Helper: parse a single geo_loc_name string, return the first result row."""
    result = ge.parse(pd.Series([value]))
    return result.iloc[0]


_GEO_COLUMNS = {"geo_country", "geo_region", "geo_locality", "geo_iso3166", "geo_sea_ocean"}


class TestCountryColonRegionLocality:
    def test_full_three_part(self, ge):
        row = _parse(ge, "Russia: Moscow, Butovo")
        assert row["geo_country"] == "Russia"
        assert row["geo_region"] == "Moscow"
        assert row["geo_locality"] == "Butovo"
        assert row["geo_iso3166"] == "RU"
        assert pd.isna(row["geo_sea_ocean"])

    def test_country_colon_region_only(self, ge):
        row = _parse(ge, "France: Ile-de-France")
        assert row["geo_country"] == "France"
        assert row["geo_region"] == "Ile-de-France"
        assert pd.isna(row["geo_locality"])

    def test_country_only(self, ge):
        row = _parse(ge, "Germany")
        assert row["geo_country"] == "Germany"
        assert row["geo_iso3166"] == "DE"
        assert pd.isna(row["geo_region"])
        assert pd.isna(row["geo_locality"])

    def test_country_colon_no_comma_region_only(self, ge):
        """Colon present, remainder has no comma -> region only, no locality.
        geo_country preserves the raw token from the input string."""
        row = _parse(ge, "USA: New York")
        assert row["geo_country"] == "USA"
        assert row["geo_region"] == "New York"
        assert pd.isna(row["geo_locality"])


class TestFallbackNoColon:
    def test_country_comma_locality_fallback(self, ge):
        row = _parse(ge, "Brazil, São Paulo")
        assert row["geo_country"] == "Brazil"
        assert row["geo_locality"] == "São Paulo"

    def test_paren_comma_guard_not_split(self, ge):
        """Comma inside parentheses must not trigger the fallback split."""
        row = _parse(ge, "United States (New York, NY)")
        assert row["geo_country"] == "United States"
        assert pd.isna(row["geo_locality"])
        assert row["geo_iso3166"] == "US"


class TestNullValues:
    @pytest.mark.parametrize("raw", [
        "missing", "unknown", "not provided", "NA", "N/A", "none", "null",
    ])
    def test_null_returns_all_nan(self, ge, raw):
        row = _parse(ge, raw)
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])


class TestOceanSea:
    @pytest.mark.parametrize("ocean", [
        "Pacific Ocean",
        "Mediterranean Sea",
        "Baltic Sea",
        "Persian Gulf",
        "Bay of Bengal",
    ])
    def test_ocean_routed_to_geo_sea_ocean(self, ge, ocean):
        row = _parse(ge, ocean)
        assert row["geo_sea_ocean"] == ocean
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])

    def test_ocean_with_locality_colon_only(self, ge):
        """Colon + no comma in sub-part: sub-part becomes geo_locality directly."""
        row = _parse(ge, "Atlantic Ocean: offshore station 12")
        assert row["geo_sea_ocean"] == "Atlantic Ocean"
        assert row["geo_locality"] == "offshore station 12"

    def test_ocean_with_colon_and_comma_subpart(self, ge):
        """Colon + comma in sub-part: both pieces concatenated into geo_locality."""
        row = _parse(ge, "Pacific Ocean: station A, depth 200m")
        assert row["geo_sea_ocean"] == "Pacific Ocean"
        assert row["geo_locality"] == "station A, depth 200m"
        assert pd.isna(row["geo_country"])

    @pytest.mark.parametrize("arctic_sea", [
        "Kara Sea",
        "Laptev Sea",
        "East Siberian Sea",
        "Chukchi Sea",
        "Beaufort Sea",
        "White Sea",
        "Greenland Sea",
    ])
    def test_arctic_seas_routed_to_geo_sea_ocean(self, ge, arctic_sea):
        """Arctic and marginal seas must land in geo_sea_ocean, not geo_country."""
        row = _parse(ge, arctic_sea)
        assert row["geo_sea_ocean"] == arctic_sea
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])

    def test_truly_unknown_water_body_returns_all_nan(self, ge):
        """A water body not in _OCEAN_SEA and not resolvable by pycountry
        returns all-NaN (nothing stored in geo_country or geo_sea_ocean)."""
        row = _parse(ge, "Lake Baikal")
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_sea_ocean"])
        assert pd.isna(row["geo_iso3166"])


class TestIsoResolution:
    @pytest.mark.parametrize("country,expected_iso", [
        ("United States", "US"),
        ("China",         "CN"),
        ("India",         "IN"),
        ("Turkey",        "TR"),
        ("Türkiye",       "TR"),
        ("Namibia",       "NA"),
        ("England",       "GB"),
        ("Scotland",      "GB"),
        ("Wales",         "GB"),
        ("Taiwan",        "TW"),
        ("Korea",         "KR"),
        ("North Korea",   "KP"),
    ])
    def test_iso_resolution(self, ge, country, expected_iso):
        row = _parse(ge, country)
        assert row["geo_iso3166"] == expected_iso

    def test_united_kingdom_display_name_for_england(self, ge):
        row = _parse(ge, "England")
        assert row["geo_country"] == "United Kingdom"

    def test_short_country_string_returns_nan(self, ge):
        """Strings shorter than 3 chars cannot be reliably resolved."""
        row = _parse(ge, "US")
        assert pd.isna(row["geo_iso3166"])

    def test_free_text_unrecognised_returns_all_nan(self, ge):
        """Completely unrecognisable strings return all-NaN."""
        row = _parse(ge, "Clinical lab sample")
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])

    @pytest.mark.parametrize("historical", [
        "USSR",
        "Yugoslavia",
        "East Germany",
        "Czechoslovakia",
        "Zaire",
    ])
    def test_historical_countries_tagged(self, ge, historical):
        row = _parse(ge, historical)
        assert row["geo_iso3166"] == "HISTORICAL"


class TestCountryAliases:
    @pytest.mark.parametrize("alias,expected_iso", [
        ("Palestine",          "PS"),
        ("Gaza Strip",         "PS"),
        ("West Bank",          "PS"),
        ("Burma",              "MM"),
        ("Myanmar (Burma)",    "MM"),
        ("DRC",                "CD"),
        ("Congo-Kinshasa",     "CD"),
        ("DR Congo",           "CD"),
    ])
    def test_country_alias_iso(self, ge, alias, expected_iso):
        row = _parse(ge, alias)
        assert row["geo_iso3166"] == expected_iso


class TestUKSubcountry:
    @pytest.mark.parametrize("subcountry", [
        "England", "Scotland", "Wales", "Northern Ireland",
    ])
    def test_uk_subcountry_display_name(self, ge, subcountry):
        row = _parse(ge, subcountry)
        assert row["geo_country"] == "United Kingdom"
        assert row["geo_iso3166"] == "GB"


class TestCoordinateEntries:
    @pytest.mark.parametrize("coord", [
        "55.7558 N, 37.6173 E",
        "40.7128 N 74.0060 W",
        "51.5074N, 0.1278W",
    ])
    def test_coordinate_string_returns_all_nan(self, ge, coord):
        """Coordinate strings are unparseable: geo_loc_raw was removed;
        all five output columns should be NaN."""
        row = _parse(ge, coord)
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])
        assert pd.isna(row["geo_sea_ocean"])
        assert "geo_loc_raw" not in row.index


class TestParenthesisStripping:
    def test_trailing_paren_stripped_from_country(self, ge):
        row = _parse(ge, "Russia (European part)")
        assert row["geo_iso3166"] == "RU"

    def test_multi_paren_strips_only_last(self, ge):
        """_PAREN_RE strips only the last parenthetical group."""
        row = _parse(ge, "United Kingdom (Great Britain) (island)")
        assert row["geo_iso3166"] == "GB"

    def test_paren_with_inner_comma_stripped(self, ge):
        """Paren containing a comma must be stripped cleanly as a single unit."""
        row = _parse(ge, "Russia (European part, west)")
        assert row["geo_iso3166"] == "RU"


class TestNaNInput:
    def test_nan_row_returns_all_nan(self, ge):
        result = ge.parse(pd.Series([np.nan]))
        row = result.iloc[0]
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])


class TestDeduplication:
    def test_repeated_country_parsed_correctly(self, ge):
        s = pd.Series(["Russia", "Russia", "Russia"])
        result = ge.parse(s)
        assert list(result["geo_iso3166"]) == ["RU", "RU", "RU"]


class TestParseDataFrameInput:
    def test_dataframe_first_column_used(self, ge):
        """parse() accepts a DataFrame and reads its first column."""
        df = pd.DataFrame({"geo_loc_name": ["France", "Germany"], "other": [1, 2]})
        result = ge.parse(df)
        assert list(result["geo_iso3166"]) == ["FR", "DE"]


class TestSeriesIndexPreservation:
    def test_non_default_index_preserved(self, ge):
        """Output DataFrame index must match the input series index."""
        s = pd.Series(["Japan", "Canada"], index=[10, 20])
        result = ge.parse(s)
        assert list(result.index) == [10, 20]
        assert result.loc[10, "geo_iso3166"] == "JP"
        assert result.loc[20, "geo_iso3166"] == "CA"


class TestEmptySeries:
    def test_empty_series_returns_empty_dataframe(self, ge):
        """Empty input must return a zero-row DataFrame with all five geo columns."""
        result = ge.parse(pd.Series([], dtype=str))
        assert result.empty
        assert _GEO_COLUMNS.issubset(set(result.columns))
