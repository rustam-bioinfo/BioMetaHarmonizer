"""
Module 2: Key Harmonization (fixed-schema approach).

Resolves raw DataFrame column names to a fixed set of NCBI standard keys
defined in schemas/unified.json. Resolution uses the shared two-layer synonym
lookup from synonyms.py, so ingestion and key harmonization always use the
same dictionary.

In the fixed-schema design, KeyMapper no longer drops or creates columns.
It only renames raw columns that may appear in non-ingestion/custom workflows,
coalesces duplicates, and reindexes to the fixed schema.
Columns not in the final schema are expected to be preserved upstream in
`_extra_attributes` rather than carried as standalone columns.
"""

import json
import logging
from pathlib import Path

import pandas as pd

from biometaharmonizer.synonyms import build_synonym_lookup, _schemas_dir
from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA


logger = logging.getLogger(__name__)


_PROTECTED_COLUMNS = frozenset(BIOSAMPLE_SCHEMA)

_SCHEMAS_DIR = _schemas_dir()


class KeyMapper:
    def __init__(self):
        self._exact = build_synonym_lookup()

        schema_path = _SCHEMAS_DIR / "unified.json"
        self._target_keys = set()
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as fh:
                schema = json.load(fh)
            for field in schema.get("fields", []):
                self._target_keys.add(field["standard_key"])

    def map_columns(self, df):
        """
        Harmonize column names for custom/non-ingestion workflows.

        Renames columns whose lowercased names match a synonym in the unified
        schema, then coalesces any duplicate column names that result from
        renaming, and finally reindexes to the canonical BIOSAMPLE_SCHEMA.

        WARNING: reindex() silently drops any column not present in
        BIOSAMPLE_SCHEMA. If you have extra computed columns you want to
        preserve, encode them into _extra_attributes before calling
        map_columns().
        """
        rename_map = {}
        for col in df.columns:
            # LOW-2: guard against mixed-case protected column names --
            # check original col name, not col_lower, so e.g.
            # 'BiOsAmPlE_AcCeSSiOn' (already canonical content, wrong case)
            # is skipped rather than incorrectly matched as a synonym target.
            if col in _PROTECTED_COLUMNS:
                continue
            col_lower = col.lower().strip()
            if col_lower in self._exact:
                target = self._exact[col_lower]
                if target in _PROTECTED_COLUMNS and target != col:
                    rename_map[col] = target

        df = df.rename(columns=rename_map)
        df = self._coalesce_duplicates(df)

        # HIGH-6: warn about columns that will be dropped by reindex.
        schema_set = _PROTECTED_COLUMNS
        extra_cols = [c for c in df.columns if c not in schema_set]
        if extra_cols:
            logger.warning(
                "map_columns(): %d column(s) not in BIOSAMPLE_SCHEMA will be dropped "
                "by reindex: %s. Encode them into _extra_attributes before calling "
                "map_columns() to preserve them.",
                len(extra_cols), extra_cols[:10],
            )

        df = df.reindex(columns=BIOSAMPLE_SCHEMA)
        return df

    def _coalesce_duplicates(self, df):
        if not df.columns.duplicated().any():
            return df

        output_cols = {}
        duped_keys = set(df.columns[df.columns.duplicated(keep=False)])

        for col in df.columns.unique():
            block = df[col]
            if isinstance(block, pd.DataFrame):
                coalesced = block.iloc[:, 0].copy()
                for i in range(1, block.shape[1]):
                    coalesced = coalesced.combine_first(block.iloc[:, i])
                output_cols[col] = coalesced
            else:
                output_cols[col] = block

        logger.info("Coalesced duplicate columns: %s", sorted(duped_keys))
        return pd.DataFrame(output_cols, index=df.index)
