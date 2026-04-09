import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.date_engine import DateEngine


@pytest.fixture
def engine():
    return DateEngine()


# --- Full ISO dates ---

class TestFullDates:

    def test_iso_date_passthrough(self, engine):
        s = pd.Series(["2021-05-12"])
        assert engine.parse(s)[0] == "2021-05-12"

    def test_real_bcereus_date_2008(self, engine):
        s = pd.Series(["2008-04-30"])
        assert engine.parse(s)[0] == "2008-04-30"

    def test_real_bcereus_date_2016(self, engine):
        s = pd.Series(["2016-08-18"])
        assert engine.parse(s)[0] == "2016-08-18"

    def test_real_bcereus_date_2021(self, engine):
        s = pd.Series(["2021-08-16"])
        assert engine.parse(s)[0] == "2021-08-16"

    def test_slash_separator(self, engine):
        s = pd.Series(["2021/05/12"])
        assert engine.parse(s)[0] == "2021-05-12"


# --- Year-only ---

class TestYearOnly:

    def test_real_bcereus_year_2009(self, engine):
        s = pd.Series(["2009"])
        assert engine.parse(s)[0] == "2009"

    def test_real_bcereus_year_2016(self, engine):
        s = pd.Series(["2016"])
        assert engine.parse(s)[0] == "2016"

    def test_year_only_general(self, engine):
        s = pd.Series(["2018"])
        assert engine.parse(s)[0] == "2018"


# --- Year-month partial dates ---

class TestYearMonth:

    def test_real_bcereus_year_month_2020_03(self, engine):
        s = pd.Series(["2020-03"])
        assert engine.parse(s)[0] == "2020-03"

    def test_year_month_slash(self, engine):
        s = pd.Series(["2020/03"])
        assert engine.parse(s)[0] == "2020-03"

    def test_month_name_year(self, engine):
        s = pd.Series(["May-2021"])
        assert engine.parse(s)[0] == "2021-05"

    def test_month_name_year_spaced(self, engine):
        s = pd.Series(["March 2019"])
        assert engine.parse(s)[0] == "2019-03"


# --- INSDC date range ---

class TestINSDCRange:

    def test_year_range(self, engine):
        s = pd.Series(["2019/2020"])
        result = engine.parse_with_range(s)
        assert result["collection_date"][0] == "2019"
        assert result["collection_date_range"][0] == "2019/2020"

    def test_year_month_range(self, engine):
        s = pd.Series(["2019-01/2020-03"])
        result = engine.parse_with_range(s)
        assert result["collection_date"][0] == "2019-01"
        assert result["collection_date_range"][0] == "2019-01/2020-03"

    def test_range_parse_returns_start(self, engine):
        s = pd.Series(["2019/2020"])
        assert engine.parse(s)[0] == "2019"


# --- Two-digit year guard ---

class TestTwoDigitYearGuard:

    def test_two_digit_19_returns_nan(self, engine):
        s = pd.Series(["19"])
        assert pd.isna(engine.parse(s)[0])

    def test_two_digit_99_returns_nan(self, engine):
        s = pd.Series(["99"])
        assert pd.isna(engine.parse(s)[0])


# --- Null-like strings (from real NCBI data) ---

class TestNullValues:

    def test_real_ncbi_not_collected(self, engine):
        s = pd.Series(["not collected"])
        assert pd.isna(engine.parse(s)[0])

    def test_missing_string(self, engine):
        s = pd.Series(["missing"])
        assert pd.isna(engine.parse(s)[0])

    def test_na_string(self, engine):
        s = pd.Series(["N/A"])
        assert pd.isna(engine.parse(s)[0])

    def test_unknown_string(self, engine):
        s = pd.Series(["unknown"])
        assert pd.isna(engine.parse(s)[0])

    def test_not_provided_string(self, engine):
        s = pd.Series(["not provided"])
        assert pd.isna(engine.parse(s)[0])

    def test_none_string(self, engine):
        s = pd.Series(["none"])
        assert pd.isna(engine.parse(s)[0])

    def test_nan_input(self, engine):
        s = pd.Series([np.nan])
        assert pd.isna(engine.parse(s)[0])

    def test_empty_string(self, engine):
        s = pd.Series([""])
        assert pd.isna(engine.parse(s)[0])

    def test_insdc_missing_control_sample(self, engine):
        s = pd.Series(["missing: control sample"])
        assert pd.isna(engine.parse(s)[0])

    def test_insdc_not_applicable_lab_stock(self, engine):
        s = pd.Series(["not applicable: lab stock"])
        assert pd.isna(engine.parse(s)[0])

    def test_restricted_access(self, engine):
        s = pd.Series(["restricted access"])
        assert pd.isna(engine.parse(s)[0])


# --- Mixed series ---

class TestMixedSeries:

    def test_real_bcereus_mixed_column(self, engine):
        s = pd.Series(["2009", "2008-04-30", "2016", "not collected", "2016-08-18", "2020-03", "2021-08-16"])
        result = engine.parse(s)
        assert result[0] == "2009"
        assert result[1] == "2008-04-30"
        assert result[2] == "2016"
        assert pd.isna(result[3])
        assert result[4] == "2016-08-18"
        assert result[5] == "2020-03"
        assert result[6] == "2021-08-16"

    def test_output_is_series(self, engine):
        s = pd.Series(["2021", "2020-03", np.nan])
        result = engine.parse(s)
        assert isinstance(result, pd.Series)
        assert len(result) == 3
