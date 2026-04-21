"""
Module 2: Key Harmonization (fixed-schema approach).

Resolves raw DataFrame column names to a fixed set of NCBI standard keys
defined in schemas/unified.json. Resolution uses the shared two-layer synonym
lookup from synonyms.py, so ingestion and key harmonization always use the
same dictionary.

In the fixed-schema design, KeyMapper no longer drops or creates columns.
It only renames raw columns that may appear in non-ingestion/custom workflows,
coalesces duplicates, and reports per-package mandatory field completeness.
Columns not in the final schema are expected to be preserved upstream in
`_extra_attributes` rather than carried as standalone columns.
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd

from biometaharmonizer.synonyms import build_synonym_lookup, _schemas_dir
from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA


logger = logging.getLogger(__name__)


_PROTECTED_COLUMNS = frozenset(BIOSAMPLE_SCHEMA)

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_SCHEMAS_DIR = _schemas_dir()
_DEFAULT_MANDATORY = _SCHEMAS_DIR / "mandatory_fields.json"

MIN_WARN_GROUP_SIZE = 10


class KeyMapper:
    def __init__(self, mandatory_path=None):
        self._exact = build_synonym_lookup()

        schema_path = _SCHEMAS_DIR / "unified.json"
        self._target_keys = set()
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as fh:
                schema = json.load(fh)
            for field in schema.get("fields", []):
                self._target_keys.add(field["standard_key"])

        mandatory_path = Path(mandatory_path) if mandatory_path else _DEFAULT_MANDATORY
        if mandatory_path.exists():
            with open(mandatory_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.mandatory = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            self.mandatory = {"default": ["collection_date", "geo_loc_name", "isolate"]}

    def map_columns(self, df, drop_sparse=0, drop_junk=False):
        """
        Harmonize column names for custom/non-ingestion workflows and report
        per-package mandatory field fill rates.

        In the fixed-schema pipeline, dropping columns is intentionally disabled:
        the tool must preserve all information. Any attributes outside the final
        schema should already be stored in `_extra_attributes` by ingestion.
        """
        rename_map = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if col_lower in _PROTECTED_COLUMNS:
                continue
            if col_lower in self._exact:
                target = self._exact[col_lower]
                if target in _PROTECTED_COLUMNS and target != col:
                    rename_map[col] = target

        df = df.rename(columns=rename_map)
        df = self._coalesce_duplicates(df)
        df = df.reindex(columns=BIOSAMPLE_SCHEMA)

        self.compliance_report = self._warn_missing_mandatory(df)
        return df

    def _coalesce_duplicates(self, df):
        if not df.columns.duplicated().any():
            return df

        output_cols = {}
        duped_keys = set(df.columns[df.columns.duplicated(keep=False)])

        for col in df.columns.unique():
            block = df[col]
            if isinstance(block, pd.DataFrame):
                # Multiple columns share this name: coalesce left-to-right.
                coalesced = block.iloc[:, 0].copy()
                for i in range(1, block.shape[1]):
                    coalesced = coalesced.combine_first(block.iloc[:, i])
                output_cols[col] = coalesced
            else:
                output_cols[col] = block

        logger.info("Coalesced duplicate columns: %s", sorted(duped_keys))
        return pd.DataFrame(output_cols, index=df.index)

    def _warn_missing_mandatory(self, df):
        rows = []
        if "ncbi_package" not in df.columns:
            return pd.DataFrame(
                columns=["package", "field", "total_records", "filled_records", "fill_pct", "status"]
            )

        for pkg, group in df.groupby("ncbi_package", dropna=False):
            n = len(group)
            if n < MIN_WARN_GROUP_SIZE:
                continue
            pkg_key = pkg if pkg in self.mandatory else "default"
            required = self.mandatory.get(pkg_key, [])
            for field in required:
                if field not in group.columns:
                    filled = 0
                    pct = 0.0
                else:
                    filled = int(group[field].notna().sum())
                    pct = filled / n * 100

                if pct >= 95:
                    status = "PASS"
                elif pct >= 80:
                    status = "WARN"
                else:
                    status = "FAIL"

                rows.append({
                    "package": pkg,
                    "field": field,
                    "total_records": n,
                    "filled_records": filled,
                    "fill_pct": round(pct, 1),
                    "status": status,
                })

                if status == "FAIL":
                    logger.warning(
                        "[%s] mandatory field '%s' fill rate: %d/%d (%.1f%%).",
                        pkg, field, filled, n, pct,
                    )
                elif status == "WARN":
                    logger.warning(
                        "[%s] mandatory field '%s' fill rate: %d/%d (%.1f%%) -- below 95%%.",
                        pkg, field, filled, n, pct,
                    )

        return pd.DataFrame(
            rows,
            columns=["package", "field", "total_records", "filled_records", "fill_pct", "status"],
        )
