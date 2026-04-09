import pandas as pd
import pytest
from pathlib import Path
from biometaharmonizer.output import write, write_summary


@pytest.fixture
def simple_df():
    return pd.DataFrame({
        "col_a": [1, 2, 3, 4, 5],
        "col_b": ["x", "y", "z", "w", "v"],
        "col_c": [1.1, 2.2, 3.3, 4.4, 5.5],
    })


def test_write_csv(tmp_path, simple_df):
    out = write(simple_df, tmp_path / "out.csv")
    assert out.exists()
    result = pd.read_csv(out)
    assert result.shape == simple_df.shape
    assert list(result.columns) == list(simple_df.columns)


def test_write_tsv(tmp_path, simple_df):
    out = write(simple_df, tmp_path / "out.tsv", fmt="tsv")
    assert out.exists()
    result = pd.read_csv(out, sep="\t")
    assert result.shape == simple_df.shape
    assert list(result.columns) == list(simple_df.columns)


def test_write_parquet(tmp_path, simple_df):
    out = write(simple_df, tmp_path / "out.parquet", fmt="parquet")
    assert out.exists()
    result = pd.read_parquet(out)
    assert result.shape == simple_df.shape
    assert list(result.columns) == list(simple_df.columns)


def test_write_invalid_format(tmp_path, simple_df):
    with pytest.raises(ValueError):
        write(simple_df, tmp_path / "out.json", fmt="json")


def test_write_creates_parent_dirs(tmp_path, simple_df):
    nested = tmp_path / "subdir" / "nested" / "out.csv"
    assert not nested.parent.exists()
    write(simple_df, nested)
    assert nested.exists()


def test_write_returns_path(tmp_path, simple_df):
    result = write(simple_df, tmp_path / "out.csv")
    assert isinstance(result, Path)
    assert result.exists()


def test_write_summary(tmp_path):
    df = pd.DataFrame({
        "full_col": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "half_col": [1, 2, 3, 4, 5, None, None, None, None, None],
    })
    out = write_summary(df, tmp_path / "summary.csv")
    assert out.exists()
    summary = pd.read_csv(out)
    assert list(summary.columns) == ["column_name", "non_null_count", "fill_pct"]
    full_row = summary[summary["column_name"] == "full_col"].iloc[0]
    half_row = summary[summary["column_name"] == "half_col"].iloc[0]
    assert full_row["fill_pct"] == 100.0
    assert half_row["fill_pct"] == 50.0
