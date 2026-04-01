import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.geo_engine import GeoEngine


@pytest.fixture
def engine():
    return GeoEngine()


# ─── Country-only (real NCBI data) ────────────────────────────────────────────────

class TestCountryOnly:

    def test_brazil(self, engine):
        s = pd.Series(["Brazil"])
        result = engine.parse(s)
        assert result["Country"][0] == "Brazil"
        assert result["ISO3166"][0] == "BR"
        assert pd.isna(result["Region"][0])
        assert pd.isna(result["Locality"][0])

    def test_united_kingdom(self, engine):
        s = pd.Series(["United Kingdom"])
        result = engine.parse(s)
        assert result["Country"][0] == "United Kingdom"
        assert result["ISO3166"][0] == "GB"

    def test_ethiopia(self, engine):
        s = pd.Series(["Ethiopia"])
        result = engine.parse(s)
        assert result["Country"][0] == "Ethiopia"
        assert result["ISO3166"][0] == "ET"

    def test_russia(self, engine):
        s = pd.Series(["Russia"])
        result = engine.parse(s)
        assert result["ISO3166"][0] == "RU"


# ─── Country: Region format ───────────────────────────────────────────────────

class TestCountryRegion:

    def test_peru_lima(self, engine):
        s = pd.Series(["Peru: Lima"])
        result = engine.parse(s)
        assert result["Country"][0] == "Peru"
        assert result["Region"][0] == "Lima"
        assert result["ISO3166"][0] == "PE"
        assert pd.isna(result["Locality"][0])

    def test_usa_california(self, engine):
        s = pd.Series(["USA: California"])
        result = engine.parse(s)
        assert result["Country"][0] == "USA"
        assert result["Region"][0] == "California"
        assert result["ISO3166"][0] == "US"

    def test_russia_moscow(self, engine):
        s = pd.Series(["Russia: Moscow"])
        result = engine.parse(s)
        assert result["Country"][0] == "Russia"
        assert result["Region"][0] == "Moscow"
        assert result["ISO3166"][0] == "RU"


# ─── Country: Region, Locality format ───────────────────────────────────────────

class TestCountryRegionLocality:

    def test_mexico_calnali_hidalgo(self, engine):
        s = pd.Series(["Mexico: Calnali, Hidalgo"])
        result = engine.parse(s)
        assert result["Country"][0] == "Mexico"
        assert result["Region"][0] == "Calnali"
        assert result["Locality"][0] == "Hidalgo"
        assert result["ISO3166"][0] == "MX"

    def test_russia_moscow_hospital(self, engine):
        s = pd.Series(["Russia: Moscow, hospital 4"])
        result = engine.parse(s)
        assert result["Country"][0] == "Russia"
        assert result["Region"][0] == "Moscow"
        assert result["Locality"][0] == "hospital 4"

    def test_china_guangdong_shenzhen(self, engine):
        s = pd.Series(["China: Guangdong, Shenzhen"])
        result = engine.parse(s)
        assert result["Country"][0] == "China"
        assert result["Region"][0] == "Guangdong"
        assert result["Locality"][0] == "Shenzhen"
        assert result["ISO3166"][0] == "CN"


# ─── Null string guard (real NCBI values) ───────────────────────────────────────

class TestNullStrings:

    def test_not_collected(self, engine):
        s = pd.Series(["not collected"])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])
        assert pd.isna(result["ISO3166"][0])

    def test_not_applicable(self, engine):
        s = pd.Series(["not applicable"])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])

    def test_missing_string(self, engine):
        s = pd.Series(["missing"])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])

    def test_unknown_string(self, engine):
        s = pd.Series(["unknown"])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])


# ─── Null and edge cases ─────────────────────────────────────────────────────────

class TestNullAndEdgeCases:

    def test_nan_input_returns_nan_row(self, engine):
        s = pd.Series([np.nan])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])
        assert pd.isna(result["ISO3166"][0])

    def test_unresolvable_country_iso_is_nan(self, engine):
        s = pd.Series(["Narnia"])
        result = engine.parse(s)
        assert result["Country"][0] == "Narnia"
        assert pd.isna(result["ISO3166"][0])

    def test_whitespace_stripped(self, engine):
        s = pd.Series(["  Russia: Moscow  "])
        result = engine.parse(s)
        assert result["Country"][0] == "Russia"
        assert result["Region"][0] == "Moscow"

    def test_output_is_dataframe(self, engine):
        s = pd.Series(["Brazil", "Peru: Lima"])
        result = engine.parse(s)
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["Country", "Region", "Locality", "ISO3166"]

    def test_index_preserved(self, engine):
        s = pd.Series(["Brazil", "Peru: Lima"], index=[10, 20])
        result = engine.parse(s)
        assert list(result.index) == [10, 20]


# ─── Mixed real series ───────────────────────────────────────────────────────────

class TestMixedRealSeries:

    def test_full_bcereus_geo_column(self, engine):
        s = pd.Series([
            "Brazil", "Peru: Lima", "United Kingdom",
            "Ethiopia", "USA: California", "Mexico: Calnali, Hidalgo"
        ])
        result = engine.parse(s)
        assert result["ISO3166"][0] == "BR"
        assert result["Region"][1] == "Lima"
        assert result["ISO3166"][2] == "GB"
        assert result["ISO3166"][3] == "ET"
        assert result["Region"][4] == "California"
        assert result["Region"][5] == "Calnali"
        assert result["Locality"][5] == "Hidalgo"

    def test_null_strings_in_real_data(self, engine):
        s = pd.Series(["not collected", "not applicable", "missing", "USA: California"])
        result = engine.parse(s)
        assert pd.isna(result["Country"][0])
        assert pd.isna(result["Country"][1])
        assert pd.isna(result["Country"][2])
        assert result["Country"][3] == "USA"
