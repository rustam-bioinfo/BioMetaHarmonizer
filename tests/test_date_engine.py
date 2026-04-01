import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.date_engine import DateEngine


@pytest.fixture
def engine():
    return DateEngine()


def test_full_date(engine):
    s = pd.Series(["2021-05-12"])
    assert engine.parse(s)[0] == "2021-05-12"


def test_year_only(engine):
    s = pd.Series(["2018"])
    assert engine.parse(s)[0] == "2018-XX-XX"


def test_month_year_string(engine):
    s = pd.Series(["May-2021"])
    assert engine.parse(s)[0] == "2021-05-XX"


def test_null_string(engine):
    s = pd.Series(["missing", "N/A", "unknown", "not collected"])
    result = engine.parse(s)
    assert all(pd.isna(result))


def test_nan_input(engine):
    s = pd.Series([np.nan])
    assert pd.isna(engine.parse(s)[0])
