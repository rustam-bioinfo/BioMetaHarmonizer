"""
Module 2: Key Harmonization (fixed-schema approach).

Resolves raw DataFrame column names to a fixed set of NCBI standard keys
defined in schemas/unified.json.  Resolution uses two exact-match layers:

  Layer 1 (schema synonyms): the synonym lists in unified.json.
  Layer 2 (authoritative):   the NCBI BioSample attribute harmonization table
      (schemas/ncbi_attributes.xml), if present.  Every known synonym maps to
      its canonical HarmonizedName.

Both layers are sourced from the shared build_synonym_lookup() in synonyms.py,
so ingestion and key harmonization always use an identical, complete dictionary.

When the NCBI XML cache is absent (e.g. fresh install), KeyMapper falls back
to unified.json synonyms only.

When multiple raw columns map to the same standard key, they are coalesced
(first non-null value wins).

Mandatory field validation is per-package: each record's ncbi_package value is
looked up in mandatory_fields.json and fill rates are reported per package.

Protected structural columns (biosample_accession, taxonomy_id, etc.) are
never renamed, dropped, or coalesced regardless of threshold settings.
The set also includes columns added by downstream pipeline steps so that a
custom pipeline calling map_columns() on an already-enriched DataFrame is safe.
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd

from biometaharmonizer.synonyms import build_synonym_lookup, _schemas_dir


logger = logging.getLogger(__name__)


_PROTECTED_COLUMNS = frozenset([
    # Structural BioSample fields
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
    "_extra_attributes",
    # Downstream pipeline output columns
    "collection_date_range",
    "geo_country", "geo_region", "geo_locality",
    "geo_iso3166", "geo_sea_ocean", "geo_loc_raw",
    "one_health_category",
])

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')
_EMAIL_RE       = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_SCHEMAS_DIR       = _schemas_dir()
_DEFAULT_MANDATORY = _SCHEMAS_DIR / "mandatory_fields.json"

MIN_WARN_GROUP_SIZE = 10


class KeyMapper:
    """
    Module 2: Key Harmonization (fixed-schema).

    Maps raw DataFrame column names to NCBI standard keys using exact synonym
    lookup from the shared two-layer synonym table (synonyms.py).

    Parameters
    ----------
    mandatory_path : str or Path, optional
        Override path to mandatory_fields.json.
    """

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

    def map_columns(self, df, drop_sparse=5, drop_junk=True):
        """
        Harmonize column names, coalesce duplicates, clean junk/sparse
        columns, and report per-package mandatory field fill rates.

        Parameters
        ----------
        df : pd.DataFrame
            Raw ingested DataFrame.
        drop_sparse : int or float, default 5
            Drop columns whose non-null count falls below this threshold.
            An integer value is treated as an absolute row count.
            A float strictly between 0 and 1 is treated as a fractional fill
            rate and drops columns below that fraction of non-null values.
            Set to 0 (or 0.0) to disable. Protected columns are never dropped.
            Passing True is treated as integer 1 and a TypeError is raised to
            prevent silent data destruction.
        drop_junk : bool, default True
            Drop columns whose names look like person names or email artifacts.
        """
        if not isinstance(drop_sparse, (int, float)):
            raise TypeError(
                f"drop_sparse must be int or float, got {type(drop_sparse).__name__}. "
                "Pass 0 to disable."
            )
        if drop_sparse is True or (isinstance(drop_sparse, int) and drop_sparse is True):
            raise TypeError(
                "drop_sparse=True is not valid. Pass an int row count or float fill "
                "rate. Pass 0 to disable."
            )

        rename_map = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if col_lower in _PROTECTED_COLUMNS:
                continue
            if col_lower in self._exact:
                target = self._exact[col_lower]
                if target != col:
                    rename_map[col] = target

        df = df.rename(columns=rename_map)
        df = self._coalesce_duplicates(df)

        if drop_junk:
            df = self._drop_junk_columns(df)

        if drop_sparse and drop_sparse > 0:
            df = self._drop_sparse_columns(df, threshold=drop_sparse)

        self.compliance_report = self._warn_missing_mandatory(df)
        return df

    def _drop_junk_columns(self, df):
        junk = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and (_PERSON_NAME_RE.match(col) or _EMAIL_RE.match(col))
        ]
        if junk:
            logger.info(
                "Dropping %d junk columns (person names / email artifacts): %s",
                len(junk), junk,
            )
            df = df.drop(columns=junk)
        return df

    def _drop_sparse_columns(self, df, threshold):
        n_rows = len(df)
        if isinstance(threshold, float) and 0.0 < threshold < 1.0:
            min_required = int(n_rows * threshold)
        else:
            min_required = int(threshold)

        non_null = df.notna().sum()
        sparse = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and non_null[col] < min_required
        ]
        if sparse:
            logger.info(
                "Dropping %d sparse columns (< %d non-null values).",
                len(sparse), min_required,
            )
            df = df.drop(columns=sparse)
        return df

    def _coalesce_duplicates(self, df):
        if not df.columns.duplicated().any():
            return df

        output_cols = {}
        duped_keys = set(df.columns[df.columns.duplicated(keep=False)])

        for col in df.columns.unique():
            if col in duped_keys:
                block = df[col]
                # df[col] returns a Series when there is exactly one column
                # with that name (edge case when keep=False over-marks).
                if isinstance(block, pd.Series):
                    output_cols[col] = block
                else:
                    coalesced = block.iloc[:, 0].copy()
                    for i in range(1, block.shape[1]):
                        coalesced = coalesced.combine_first(block.iloc[:, i])
                    output_cols[col] = coalesced
            else:
                output_cols[col] = df[col]

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
