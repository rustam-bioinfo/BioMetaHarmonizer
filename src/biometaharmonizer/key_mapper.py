"""
Module 2: Key Harmonization (fixed-schema approach).

Resolves raw DataFrame column names to a fixed set of NCBI standard keys
defined in schemas/unified.json.  Resolution uses two exact-match layers:

  Layer 1 (authoritative): the NCBI BioSample attribute harmonization table
      (schemas/ncbi_attributes.xml), if present.  Every known synonym maps to
      its canonical HarmonizedName.

  Layer 2 (schema synonyms): the synonym lists in unified.json.

Both layers are exact (case-insensitive) lookups -- no embedding models, no
semantic similarity.  This eliminates the sentence-transformers dependency and
the one-time build_ncbi_attribute_cache.py step.

When the NCBI XML cache is absent (e.g. fresh install before the optional
build step), KeyMapper falls back to unified.json synonyms only.  This is
sufficient for the vast majority of real-world column names.

When multiple raw columns map to the same standard key, they are coalesced
(first non-null value wins).

Mandatory field validation is per-package: each record's ncbi_package value is
looked up in mandatory_fields.json and fill rates are reported per package.
"""

import importlib.resources
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)


_PROTECTED_COLUMNS = frozenset([
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
    "_extra_attributes",
])

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')
_EMAIL_RE       = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _schemas_dir() -> Path:
    try:
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


_SCHEMAS_DIR       = _schemas_dir()
_XML_CACHE         = _SCHEMAS_DIR / "ncbi_attributes.xml"
_UNIFIED_SCHEMA    = _SCHEMAS_DIR / "unified.json"
_DEFAULT_MANDATORY = _SCHEMAS_DIR / "mandatory_fields.json"


MIN_WARN_GROUP_SIZE = 10


def _build_synonym_lookup(xml_path: Path, schema_path: Path) -> dict:
    """
    Build a combined {lowercased_synonym: harmonized_name} lookup from:
      1. The NCBI BioSample attribute XML (if present).
      2. The unified.json synonym lists.

    unified.json synonyms are loaded first; NCBI XML synonyms are merged on
    top so that the official NCBI mapping always wins when both define the
    same synonym.
    """
    lookup: dict[str, str] = {}

    # --- unified.json synonyms ---
    target_keys = set()
    if schema_path.exists():
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        for field in schema.get("fields", []):
            sk = field["standard_key"]
            target_keys.add(sk)
            lookup[sk.lower()] = sk
            for syn in field.get("synonyms", []):
                syn_lower = syn.lower().strip()
                if syn_lower:
                    lookup[syn_lower] = sk

    # --- NCBI XML synonyms (optional, layered on top) ---
    if xml_path.exists():
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            for attr in root.iter("Attribute"):
                hn_el = attr.find("HarmonizedName")
                if hn_el is None or not hn_el.text:
                    continue
                hn = hn_el.text.strip()
                lookup[hn.lower()] = hn
                for syn_el in attr.findall("Synonym"):
                    if syn_el.text and syn_el.text.strip():
                        lookup[syn_el.text.strip().lower()] = hn
        except ET.ParseError:
            logger.warning("Could not parse NCBI attribute XML; using unified.json only.")

    return lookup


class KeyMapper:
    """
    Module 2: Key Harmonization (fixed-schema).

    Maps raw DataFrame column names to NCBI standard keys using exact synonym
    lookup from the NCBI BioSample attribute XML and unified.json.

    Parameters
    ----------
    mandatory_path : str or Path, optional
        Override path to mandatory_fields.json.
    """

    def __init__(self, mandatory_path=None):
        import biometaharmonizer.key_mapper as _this_module

        xml_cache   = _this_module._XML_CACHE
        schema_path = _this_module._UNIFIED_SCHEMA

        self._exact = _build_synonym_lookup(xml_cache, schema_path)

        # Load target keys from unified.json for reference
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
            A float between 0 and 1 is treated as a fractional fill rate.
            Set to 0 to disable. Protected structural columns are never dropped.
        drop_junk : bool, default True
            Drop columns whose names look like person names or email artifacts.
        """
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
            print(f"[INFO] Dropping {len(junk)} junk columns (person names / email artifacts): {junk}")
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
            print(f"[INFO] Dropping {len(sparse)} sparse columns (< {min_required} non-null values).")
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
                coalesced = block.iloc[:, 0].copy()
                for i in range(1, block.shape[1]):
                    coalesced = coalesced.combine_first(block.iloc[:, i])
                output_cols[col] = coalesced
            else:
                output_cols[col] = df[col]

        print(f"[INFO] Coalesced duplicate columns: {sorted(duped_keys)}")
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
                        "[%s] mandatory field '%s' fill rate: %d/%d (%.0f%%).",
                        pkg, field, filled, n, pct,
                    )
                elif status == "WARN":
                    logger.warning(
                        "[%s] mandatory field '%s' fill rate: %d/%d (%.0f%%) -- below 95%%.",
                        pkg, field, filled, n, pct,
                    )

        return pd.DataFrame(rows, columns=["package", "field", "total_records", "filled_records", "fill_pct", "status"])
