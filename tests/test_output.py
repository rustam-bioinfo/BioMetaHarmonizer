"""
Tests for output.py.

All tests use synthetic DataFrames written to a temporary directory.
No live NCBI calls are made.
"""

import json
import math
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.output import write, write_summary


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "biosample_accession": ["SAMN001", "SAMN002", "SAMN003"],
        "organism_name":       ["Escherichia coli", "Salmonella enterica", None],
        "collection_date":     ["2021-01", None, "2019-06-15"],
    })


@pytest.fixture
def single_row_df():
    return pd.DataFrame({
        "biosample_accession": ["SAMN001"],
        "organism_name":       ["Escherichia coli"],
    })


@pytest.fixture
def empty_df():
    return pd.DataFrame({
        "biosample_accession": pd.Series([], dtype=str),
        "organism_name":       pd.Series([], dtype=str),
    })


# ---------------------------------------------------------------------------
# write -- CSV
# ---------------------------------------------------------------------------

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

    def test_fmt_case_insensitive(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        write(sample_df, out, fmt="CSV")
        assert out.exists()

    def test_str_path_accepted(self, tmp_path, sample_df):
        out = str(tmp_path / "out.csv")
        result = write(sample_df, out, fmt="csv")
        assert isinstance(result, Path)
        assert result.exists()

    def test_returned_path_is_absolute(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        result = write(sample_df, out, fmt="csv")
        assert result.is_absolute()

    def test_returned_path_equals_resolved(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        result = write(sample_df, out, fmt="csv")
        assert result == out.resolve()

    def test_non_ascii_values_utf8(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "geo_loc_name":        ["Россия"],
        })
        out = tmp_path / "out.csv"
        write(df, out, fmt="csv")
        loaded = pd.read_csv(out, encoding="utf-8")
        assert loaded.iloc[0]["geo_loc_name"] == "Россия"

    def test_none_values_written_as_empty_cell(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        write(sample_df, out, fmt="csv")
        loaded = pd.read_csv(out)
        assert pd.isna(loaded.iloc[1]["organism_name"])

    def test_overwrite_replaces_file(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        write(sample_df, out, fmt="csv")
        df2 = pd.DataFrame({"biosample_accession": ["SAMN999"]})
        write(df2, out, fmt="csv")
        loaded = pd.read_csv(out)
        assert list(loaded["biosample_accession"]) == ["SAMN999"]

    def test_empty_dataframe_zero_rows(self, tmp_path, empty_df):
        out = tmp_path / "empty.csv"
        write(empty_df, out, fmt="csv")
        loaded = pd.read_csv(out)
        assert len(loaded) == 0
        assert "biosample_accession" in loaded.columns


# ---------------------------------------------------------------------------
# write -- TSV
# ---------------------------------------------------------------------------

class TestWriteTSV:
    def test_tsv_delimiter(self, tmp_path, sample_df):
        out = tmp_path / "out.tsv"
        write(sample_df, out, fmt="tsv")
        text = out.read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert "\t" in header

    def test_tsv_round_trip(self, tmp_path, sample_df):
        out = tmp_path / "out.tsv"
        write(sample_df, out, fmt="tsv")
        loaded = pd.read_csv(out, sep="\t")
        assert list(loaded["biosample_accession"]) == ["SAMN001", "SAMN002", "SAMN003"]

    def test_tsv_fmt_uppercase(self, tmp_path, sample_df):
        out = tmp_path / "out.tsv"
        write(sample_df, out, fmt="TSV")
        assert out.exists()

    def test_tsv_no_comma_delimiter_in_header(self, tmp_path, sample_df):
        out = tmp_path / "out.tsv"
        write(sample_df, out, fmt="tsv")
        header = out.read_text(encoding="utf-8").splitlines()[0]
        # commas must not be the separator
        assert header.count("\t") >= 1


# ---------------------------------------------------------------------------
# write -- JSONL
# ---------------------------------------------------------------------------

class TestWriteJSONL:
    def test_jsonl_each_line_valid_json(self, tmp_path, sample_df):
        out = tmp_path / "out.jsonl"
        write(sample_df, out, fmt="jsonl")
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "biosample_accession" in obj

    def test_jsonl_extra_attributes_string_expanded(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "_extra_attributes":   ['{"antibiogram": [{"antibiotic_name": "amikacin"}]}'],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        obj = json.loads(out.read_text(encoding="utf-8").strip())
        assert isinstance(obj["_extra_attributes"], dict)
        assert isinstance(obj["_extra_attributes"]["antibiogram"], list)

    def test_jsonl_extra_attributes_already_dict_not_double_serialized(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "_extra_attributes":   [{"key": "value"}],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        obj = json.loads(out.read_text(encoding="utf-8").strip())
        assert isinstance(obj["_extra_attributes"], dict)
        assert obj["_extra_attributes"]["key"] == "value"

    def test_jsonl_extra_attributes_none_does_not_raise(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "_extra_attributes":   [None],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        obj = json.loads(out.read_text(encoding="utf-8").strip())
        assert "biosample_accession" in obj

    def test_jsonl_extra_attributes_invalid_json_written_as_string(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "_extra_attributes":   ["{not valid json"],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        obj = json.loads(out.read_text(encoding="utf-8").strip())
        assert isinstance(obj["_extra_attributes"], str)
        assert "{not valid json" in obj["_extra_attributes"]

    def test_jsonl_non_ascii_ensure_ascii_false(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001"],
            "geo_loc_name":        ["Россия"],
        })
        out = tmp_path / "out.jsonl"
        write(df, out, fmt="jsonl")
        raw = out.read_text(encoding="utf-8")
        # With ensure_ascii=False the Cyrillic letters appear literally
        assert "Россия" in raw

    def test_jsonl_fmt_uppercase(self, tmp_path, sample_df):
        out = tmp_path / "out.jsonl"
        write(sample_df, out, fmt="JSONL")
        assert out.exists()

    def test_jsonl_empty_dataframe_produces_empty_file(self, tmp_path, empty_df):
        out = tmp_path / "empty.jsonl"
        write(empty_df, out, fmt="jsonl")
        content = out.read_text(encoding="utf-8").strip()
        assert content == ""


# ---------------------------------------------------------------------------
# write -- Excel
# ---------------------------------------------------------------------------

class TestWriteExcel:
    def test_excel_round_trip(self, tmp_path, sample_df):
        pytest.importorskip("openpyxl")
        out = tmp_path / "out.xlsx"
        write(sample_df, out, fmt="excel")
        loaded = pd.read_excel(out, engine="openpyxl")
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns

    def test_excel_fmt_uppercase(self, tmp_path, sample_df):
        pytest.importorskip("openpyxl")
        out = tmp_path / "out.xlsx"
        write(sample_df, out, fmt="EXCEL")
        assert out.exists()

    def test_excel_column_values_preserved(self, tmp_path, sample_df):
        pytest.importorskip("openpyxl")
        out = tmp_path / "out.xlsx"
        write(sample_df, out, fmt="excel")
        loaded = pd.read_excel(out, engine="openpyxl")
        assert loaded.iloc[0]["biosample_accession"] == "SAMN001"


# ---------------------------------------------------------------------------
# write -- Parquet
# ---------------------------------------------------------------------------

class TestWriteParquet:
    def test_parquet_round_trip(self, tmp_path, sample_df):
        pytest.importorskip("pyarrow")
        out = tmp_path / "out.parquet"
        write(sample_df, out, fmt="parquet")
        loaded = pd.read_parquet(out)
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns

    def test_parquet_fmt_uppercase(self, tmp_path, sample_df):
        pytest.importorskip("pyarrow")
        out = tmp_path / "out.parquet"
        write(sample_df, out, fmt="PARQUET")
        assert out.exists()

    def test_parquet_empty_dataframe(self, tmp_path, empty_df):
        pytest.importorskip("pyarrow")
        out = tmp_path / "empty.parquet"
        write(empty_df, out, fmt="parquet")
        loaded = pd.read_parquet(out)
        assert len(loaded) == 0
        assert "biosample_accession" in loaded.columns


# ---------------------------------------------------------------------------
# write -- atomic safety
# ---------------------------------------------------------------------------

class TestWriteAtomicSafety:
    def test_temp_file_cleaned_up_on_failure(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        files_before = set(tmp_path.iterdir())

        with patch("biometaharmonizer.output.tempfile.NamedTemporaryFile") as mock_ntf:
            # Make the write itself raise after the temp file context exits
            import tempfile as _tf
            real_ntf = _tf.NamedTemporaryFile(
                dir=tmp_path, suffix=".csv.tmp", delete=False
            )
            real_ntf.close()
            tmp_sentinel = Path(real_ntf.name)

            mock_ntf.return_value.__enter__ = lambda s: real_ntf
            mock_ntf.return_value.__exit__ = lambda s, *a: False
            mock_ntf.return_value.name = str(tmp_sentinel)

            # Patch to_csv to raise mid-write
            with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")):
                with pytest.raises(OSError, match="disk full"):
                    write(sample_df, out, fmt="csv")

            # Temp sentinel should be gone
            assert not tmp_sentinel.exists()

    def test_destination_not_created_on_failure(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                write(sample_df, out, fmt="csv")
        assert not out.exists()

    def test_existing_file_not_corrupted_on_failure(self, tmp_path, sample_df):
        out = tmp_path / "out.csv"
        write(sample_df, out, fmt="csv")
        original_content = out.read_text(encoding="utf-8")

        with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                write(sample_df, out, fmt="csv")

        assert out.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# write -- unsupported format
# ---------------------------------------------------------------------------

class TestWriteInvalidFormat:
    def test_unsupported_format_raises_value_error(self, tmp_path, sample_df):
        with pytest.raises(ValueError, match="Unsupported format"):
            write(sample_df, tmp_path / "out.txt", fmt="xml")

    def test_error_message_lists_valid_formats(self, tmp_path, sample_df):
        with pytest.raises(ValueError, match="csv"):
            write(sample_df, tmp_path / "out.txt", fmt="xml")

    def test_empty_string_format_raises(self, tmp_path, sample_df):
        with pytest.raises(ValueError):
            write(sample_df, tmp_path / "out.txt", fmt="")


# ---------------------------------------------------------------------------
# write_summary
# ---------------------------------------------------------------------------

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

    def test_empty_dataframe_no_zero_division(self, tmp_path, empty_df):
        out = tmp_path / "summary_empty.csv"
        write_summary(empty_df, out)
        df = pd.read_csv(out)
        assert (df["fill_pct"] == 0.0).all()

    def test_single_row_all_filled(self, tmp_path, single_row_df):
        out = tmp_path / "summary_single.csv"
        write_summary(single_row_df, out)
        df = pd.read_csv(out)
        assert (df["fill_pct"] == 100.0).all()
        assert (df["non_null_count"] == 1).all()

    def test_all_null_column(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001", "SAMN002"],
            "empty_col":           [None, None],
        })
        out = tmp_path / "summary_null.csv"
        write_summary(df, out)
        summary = pd.read_csv(out)
        row = summary[summary["column_name"] == "empty_col"].iloc[0]
        assert row["non_null_count"] == 0
        assert row["fill_pct"] == 0.0

    def test_all_filled_column(self, tmp_path):
        df = pd.DataFrame({
            "biosample_accession": ["SAMN001", "SAMN002"],
            "organism_name":       ["E. coli", "S. aureus"],
        })
        out = tmp_path / "summary_full.csv"
        write_summary(df, out)
        summary = pd.read_csv(out)
        row = summary[summary["column_name"] == "organism_name"].iloc[0]
        assert row["non_null_count"] == 2
        assert row["fill_pct"] == 100.0

    def test_column_order_matches_input(self, tmp_path):
        df = pd.DataFrame({
            "z_col": ["a"],
            "a_col": ["b"],
            "m_col": [None],
        })
        out = tmp_path / "summary_order.csv"
        write_summary(df, out)
        summary = pd.read_csv(out)
        assert list(summary["column_name"]) == ["z_col", "a_col", "m_col"]

    def test_returns_path_that_exists(self, tmp_path, sample_df):
        out = tmp_path / "summary.csv"
        result = write_summary(sample_df, out)
        assert isinstance(result, Path)
        assert result.exists()

    def test_parent_dirs_created(self, tmp_path, sample_df):
        out = tmp_path / "nested" / "deep" / "summary.csv"
        write_summary(sample_df, out)
        assert out.exists()

    def test_fill_pct_one_decimal_place(self, tmp_path):
        df = pd.DataFrame({
            "col": ["a", None, None],
        })
        out = tmp_path / "summary_decimal.csv"
        write_summary(df, out)
        summary = pd.read_csv(out)
        val = summary.iloc[0]["fill_pct"]
        # 1/3 * 100 = 33.333... -> should be rounded to one decimal
        assert val == round(val, 1)
        assert abs(val - 33.3) < 0.1
