"""
Tests for key_mapper.py.

All tests use synthetic data -- no live NCBI calls are made.
"""

import pandas as pd
import pytest

from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA
from biometaharmonizer.key_mapper import KeyMapper


@pytest.fixture(scope="module")
def km():
    return KeyMapper()


class TestReindex:
    def test_output_columns_match_biosample_schema(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001"}])
        result = km.map_columns(df)
        assert list(result.columns) == list(BIOSAMPLE_SCHEMA)

    def test_extra_columns_dropped_with_warning(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001", "extra_col": "foo"}])
        with pytest.warns(UserWarning, match="not in BIOSAMPLE_SCHEMA"):
            result = km.map_columns(df)
        assert "extra_col" not in result.columns


class TestSynonymRename:
    def test_synonym_column_renamed_to_standard_key(self, km):
        """
        'collection_date' is a standard key; a synonym variant should be
        renamed. We use a column name that exists in the synonym table and
        maps to a BIOSAMPLE_SCHEMA key.
        """
        standard_keys = set(BIOSAMPLE_SCHEMA)
        synonym_lookup = km._exact
        testable = [
            (raw, target)
            for raw, target in synonym_lookup.items()
            if target in standard_keys and raw != target
        ]
        if not testable:
            pytest.skip("No testable synonym found in synonym table")

        raw_col, target_col = testable[0]
        df = pd.DataFrame([{raw_col: "test_value"}])
        result = km.map_columns(df)
        assert target_col in result.columns
        assert result.iloc[0][target_col] == "test_value"


class TestCoalesceDuplicates:
    def test_duplicate_columns_coalesced(self, km):
        """
        When map_columns produces two columns with the same name,
        _coalesce_duplicates should combine them with combine_first semantics.
        """
        import pandas as pd
        col_a = pd.Series(["value_a", None])
        col_b = pd.Series([None, "value_b"])
        df_raw = pd.concat([col_a.rename("x"), col_b.rename("x")], axis=1)
        result = km._coalesce_duplicates(df_raw)
        assert result.shape[1] == 1
        assert result.iloc[0]["x"] == "value_a"
        assert result.iloc[1]["x"] == "value_b"

    def test_no_duplicates_unchanged(self, km):
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = km._coalesce_duplicates(df)
        assert list(result.columns) == ["a", "b"]


class TestProtectedColumns:
    def test_protected_column_not_renamed(self, km):
        """
        A column already named with a standard BIOSAMPLE_SCHEMA key must not
        be touched by the rename pass even if its name is also a synonym for
        a different standard key.
        """
        df = pd.DataFrame([{"biosample_accession": "SAMN001"}])
        result = km.map_columns(df)
        assert result.iloc[0]["biosample_accession"] == "SAMN001"
