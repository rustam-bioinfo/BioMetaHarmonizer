"""
Tests for output.py.

All tests use synthetic DataFrames written to a temporary directory.
No live NCBI calls are made.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from biometaharmonizer.output import write, write_summary


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "biosample_accession": ["SAMN001", "SAMN002", "SAMN003"],
        "organism_name":       ["Escherichia coli", "Salmonella enterica", None],
        "collection_date":     ["2021-01", None, "2019-06-15"],
    })


class TestWriteCSV:
    def test_csv_round_trip(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        write(sample_df, out, fmt="csv")
        loaded = pd.read_csv(out)
        assert list(loaded["biosample_accession"]) == ["SAMN001", "SAMN002", "SAMN003"]

    def test_returns_path_object(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        result = write(sample_df, out, fmt="csv")
        assert isinstance(result, Path)
        assert result.exists()

    def test_parent_dirs_created(self, tmp_path, sample_df):
        out = tmp_path / "subdir" / "nested" / "out.csv"
        write(sample_df, out, fmt="csv")
        assert out.exists()


class TestWriteTSV:
    def test_tsv_delimiter(self, tmp_path, sample_df):
        out = tmp_path / "out.tsv"
        write(sample_df, out, fmt="tsv")
        text = out.read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert "\t" in header


class TestWriteJSONL:
    def test_jsonl_each_line_valid_json(self, tmp_path, sample_df):
        out = tmp_path / "out.jsonl"
        write(sample_df, out, fmt="jsonl")
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "biosample_accession" in obj

    def test_jsonl_extra_attributes_expanded(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "_extra_attributes":   ['{"antibiogram": [{"antibiotic_name": "amikacin"}]}'],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        obj = json.loads(out.read_text(encoding="utf-8").strip())
        assert isinstance(obj["_extra_attributes"], dict)
        assert isinstance(obj["_extra_attributes"]["antibiogram"], list)


class TestWriteParquet:
    def test_parquet_round_trip(self, tmp_path, sample_df):
        out = tmp_path / "out.parquet"
        write(sample_df, out, fmt="parquet")
        loaded = pd.read_parquet(out)
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns


class TestWriteInvalidFormat:
    def test_unsupported_format_raises_value_error(self, tmp_path, sample_df):
        with pytest.raises(ValueError, match="Unsupported format"):
            write(sample_df, tmp_path / "out.txt", fmt="xml")


class TestWriteSummary:
    def test_summary_columns_present(self, tmp_path, sample_df):
        out = tmp_path / "summary.csv"
        write_summary(sample_df, out)
        df = pd.read_csv(out)
        assert "column_name" in df.columns
        assert "non_null_count" in df.columns
        assert "fill_pct" in df.columns

    def test_summary_row_count_matches_df_columns(self, tmp_path, sample_df):
        out = tmp_path / "summary.csv"
        write_summary(sample_df, out)
        df = pd.read_csv(out)
        assert len(df) == len(sample_df.columns)

    def test_fill_pct_range(self, tmp_path, sample_df):
        out = tmp_path / "summary.csv"
        write_summary(sample_df, out)
        df = pd.read_csv(out)
        assert (df["fill_pct"] >= 0.0).all()
        assert (df["fill_pct"] <= 100.0).all()

    def test_collection_date_partial_fill(self, tmp_path, sample_df):
        out = tmp_path / "summary.csv"
        write_summary(sample_df, out)
        df = pd.read_csv(out)
        row = df[df["column_name"] == "collection_date"].iloc[0]
        assert row["non_null_count"] == 2
        assert abs(row["fill_pct"] - 66.7) < 0.2
