"""
Tests for key_mapper.py.

All tests use synthetic data -- no live NCBI calls are made.
"""

import json
import warnings

import pandas as pd
import pytest

from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA, BIOSAMPLE_SCHEMA_SET
from biometaharmonizer.key_mapper import KeyMapper


@pytest.fixture(scope="module")
def km():
    return KeyMapper()


# ---------------------------------------------------------------------------
# TestKeyMapperInit
# ---------------------------------------------------------------------------

class TestKeyMapperInit:
    def test_exact_lookup_non_empty(self, km):
        assert len(km._exact) > 0

    def test_all_synonym_values_in_schema(self, km):
        """Every resolved value in the synonym table must be a known schema column."""
        bad = [
            (raw, target)
            for raw, target in km._exact.items()
            if target not in BIOSAMPLE_SCHEMA_SET
        ]
        assert bad == [], f"Synonyms resolving to unknown columns: {bad[:5]}"

    def test_target_keys_populated(self, km):
        assert len(km._target_keys) > 0

    def test_target_keys_contain_core_columns(self, km):
        for col in ("geo_loc_name", "collection_date", "isolation_source", "host"):
            assert col in km._target_keys


# ---------------------------------------------------------------------------
# TestMapColumnsRename
# ---------------------------------------------------------------------------

class TestMapColumnsRename:
    def _find_synonym_pair(self, km):
        """Return (raw_col, target_col) where raw != target and target in schema."""
        pairs = [
            (raw, target)
            for raw, target in km._exact.items()
            if target in BIOSAMPLE_SCHEMA_SET and raw != target
        ]
        if not pairs:
            pytest.skip("No testable synonym pair found")
        return pairs[0]

    def test_single_synonym_renamed(self, km):
        raw_col, target_col = self._find_synonym_pair(km)
        df = pd.DataFrame([{raw_col: "test_value"}])
        result = km.map_columns(df)
        assert result.iloc[0][target_col] == "test_value"

    def test_multiple_synonyms_renamed_in_one_call(self, km):
        pairs = [
            (raw, target)
            for raw, target in km._exact.items()
            if target in BIOSAMPLE_SCHEMA_SET and raw != target
        ]
        if len(pairs) < 2:
            pytest.skip("Need at least two synonym pairs")
        (raw1, tgt1), (raw2, tgt2) = pairs[0], pairs[1]
        if tgt1 == tgt2:
            pytest.skip("Both synonyms map to the same target -- pick different pair")
        df = pd.DataFrame([{raw1: "val1", raw2: "val2"}])
        result = km.map_columns(df)
        assert result.iloc[0][tgt1] == "val1"
        assert result.iloc[0][tgt2] == "val2"

    def test_case_insensitive_synonym_rename(self, km):
        raw_col, target_col = self._find_synonym_pair(km)
        upper_col = raw_col.upper()
        if upper_col == raw_col:
            upper_col = raw_col.capitalize()
        df = pd.DataFrame([{upper_col: "case_value"}])
        result = km.map_columns(df)
        assert result.iloc[0][target_col] == "case_value"

    def test_whitespace_in_column_name_renamed(self, km):
        raw_col, target_col = self._find_synonym_pair(km)
        spaced_col = f"  {raw_col}  "
        df = pd.DataFrame([{spaced_col: "spaced_value"}])
        result = km.map_columns(df)
        assert result.iloc[0][target_col] == "spaced_value"

    def test_already_standard_column_not_corrupted(self, km):
        """A column already named with a standard key must pass through unchanged."""
        df = pd.DataFrame([{"isolation_source": "soil", "biosample_accession": "SAMN001"}])
        result = km.map_columns(df)
        assert result.iloc[0]["isolation_source"] == "soil"
        assert result.iloc[0]["biosample_accession"] == "SAMN001"

    def test_self_mapping_synonym_skipped(self, km):
        """If a synonym maps to itself (raw == target) the rename is skipped.
        The column still lands in the correct schema position."""
        schema_col = "isolation_source"
        df = pd.DataFrame([{schema_col: "river"}])
        result = km.map_columns(df)
        assert result.iloc[0][schema_col] == "river"


# ---------------------------------------------------------------------------
# TestMapColumnsWarning
# ---------------------------------------------------------------------------

class TestMapColumnsWarning:
    def test_extra_column_triggers_warning(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001", "extra_col": "foo"}])
        with pytest.warns(UserWarning, match="not in BIOSAMPLE_SCHEMA"):
            km.map_columns(df)

    def test_warning_mentions_extra_column_name(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001", "my_unknown_col": "bar"}])
        with pytest.warns(UserWarning, match="my_unknown_col"):
            km.map_columns(df)

    def test_no_warning_when_no_extra_columns(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001"}])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            km.map_columns(df)

    def test_extra_column_dropped_from_output(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001", "extra_col": "foo"}])
        with pytest.warns(UserWarning):
            result = km.map_columns(df)
        assert "extra_col" not in result.columns


# ---------------------------------------------------------------------------
# TestMapColumnsEmpty
# ---------------------------------------------------------------------------

class TestMapColumnsEmpty:
    def test_empty_dataframe_columns_match_schema(self, km):
        result = km.map_columns(pd.DataFrame())
        assert list(result.columns) == list(BIOSAMPLE_SCHEMA)

    def test_empty_dataframe_zero_rows(self, km):
        result = km.map_columns(pd.DataFrame())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# TestMapColumnsColumnOrder
# ---------------------------------------------------------------------------

class TestMapColumnsColumnOrder:
    def test_output_columns_match_biosample_schema(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001"}])
        result = km.map_columns(df)
        assert list(result.columns) == list(BIOSAMPLE_SCHEMA)

    def test_column_order_stable_with_mixed_input(self, km):
        """Regardless of input column order, output always follows BIOSAMPLE_SCHEMA."""
        df = pd.DataFrame([{
            "organism_name": "E. coli",
            "biosample_accession": "SAMN001",
            "collection_date": "2020-01-01",
        }])
        result = km.map_columns(df)
        assert list(result.columns) == list(BIOSAMPLE_SCHEMA)


# ---------------------------------------------------------------------------
# TestReindex (original)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TestSynonymRename (original)
# ---------------------------------------------------------------------------

class TestSynonymRename:
    def test_synonym_column_renamed_to_standard_key(self, km):
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


# ---------------------------------------------------------------------------
# TestCoalesceDuplicates
# ---------------------------------------------------------------------------

class TestCoalesceDuplicates:
    def test_duplicate_columns_coalesced(self, km):
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

    def test_three_copies_first_non_null_wins(self, km):
        c1 = pd.Series([None, "first", None])
        c2 = pd.Series([None, None, "second"])
        c3 = pd.Series(["zero", None, None])
        df_raw = pd.concat(
            [c1.rename("y"), c2.rename("y"), c3.rename("y")], axis=1
        )
        result = km._coalesce_duplicates(df_raw)
        assert result.shape[1] == 1
        assert result.iloc[0]["y"] == "zero"
        assert result.iloc[1]["y"] == "first"
        assert result.iloc[2]["y"] == "second"

    def test_all_null_duplicate_yields_null_column(self, km):
        c1 = pd.Series([None, None])
        c2 = pd.Series([None, None])
        df_raw = pd.concat([c1.rename("z"), c2.rename("z")], axis=1)
        result = km._coalesce_duplicates(df_raw)
        assert result.shape[1] == 1
        assert result["z"].isna().all()

    def test_non_duplicate_column_order_preserved(self, km):
        """Non-duplicate columns must keep their relative order after coalescing."""
        c_dup1 = pd.Series(["a", None])
        c_dup2 = pd.Series([None, "b"])
        df_raw = pd.concat(
            [
                pd.Series([1, 2], name="first"),
                c_dup1.rename("dup"),
                c_dup2.rename("dup"),
                pd.Series([3, 4], name="last"),
            ],
            axis=1,
        )
        result = km._coalesce_duplicates(df_raw)
        assert list(result.columns) == ["first", "dup", "last"]

    def test_index_preserved_after_coalesce(self, km):
        c1 = pd.Series(["val", None], index=[10, 20])
        c2 = pd.Series([None, "val2"], index=[10, 20])
        df_raw = pd.concat([c1.rename("col"), c2.rename("col")], axis=1)
        result = km._coalesce_duplicates(df_raw)
        assert list(result.index) == [10, 20]


# ---------------------------------------------------------------------------
# TestProtectedColumns (original)
# ---------------------------------------------------------------------------

class TestProtectedColumns:
    def test_protected_column_not_renamed(self, km):
        df = pd.DataFrame([{"biosample_accession": "SAMN001"}])
        result = km.map_columns(df)
        assert result.iloc[0]["biosample_accession"] == "SAMN001"


# ---------------------------------------------------------------------------
# TestExtraAttributesSurvival
# ---------------------------------------------------------------------------

class TestExtraAttributesSurvival:
    def test_extras_encoded_before_map_columns_survive(self, km):
        """Values that would be dropped by reindex can be saved by encoding
        them into _extra_attributes before calling map_columns."""
        extras = {"my_custom_field": "preserved_value"}
        df = pd.DataFrame([{
            "biosample_accession": "SAMN001",
            "_extra_attributes": json.dumps(extras),
        }])
        result = km.map_columns(df)
        assert "_extra_attributes" in result.columns
        decoded = json.loads(result.iloc[0]["_extra_attributes"])
        assert decoded["my_custom_field"] == "preserved_value"
