"""
Module 2: Key Harmonization.

Resolves raw DataFrame column names to NCBI standard keys using a two-layer approach:

  Layer 1 (authoritative): exact/synonym lookup from the NCBI BioSample attribute
      harmonization table (schemas/ncbi_attributes.xml), built by
      scripts/build_ncbi_attribute_cache.py.

  Layer 2 (semantic fallback): cosine similarity between a sentence-transformers
      embedding of the column name and precomputed embeddings of all NCBI
      harmonized names (schemas/ncbi_embeddings.npy). Model: all-MiniLM-L6-v2.
      Threshold: SEMANTIC_THRESHOLD = 0.75. Model is loaded lazily on first use.

The NCBI attribute cache must be built before using KeyMapper:
    python scripts/build_ncbi_attribute_cache.py

Mandatory field validation is per-package: each record's ncbi_package value is
looked up in mandatory_fields.json and fill rates are reported per package rather
than as a single global check. Packages with fewer than MIN_WARN_GROUP_SIZE records
are silently skipped to avoid noise from singleton or near-singleton submissions.

When multiple raw columns map to the same standard key, they are coalesced
(first non-null value wins).
"""

import importlib.resources
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd


_PROTECTED_COLUMNS = frozenset([
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
])

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')
_EMAIL_RE       = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _schemas_dir() -> Path:
    """
    Locate the schemas/ directory in a way that works for both editable and
    non-editable installs.

    Strategy:
      1. Try importlib.resources (correct for wheel / non-editable installs).
      2. Fall back to __file__-relative path (works for editable installs and
         development checkouts where schemas/ is inside src/biometaharmonizer/).
    """
    try:
        # Python 3.9+: importlib.resources.files() returns a Traversable
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        # Materialise to a real Path so callers can use / operator freely
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


_SCHEMAS_DIR = _schemas_dir()
_XML_CACHE   = _SCHEMAS_DIR / "ncbi_attributes.xml"
_EMB_FILE    = _SCHEMAS_DIR / "ncbi_embeddings.npy"
_NAMES_FILE  = _SCHEMAS_DIR / "ncbi_harmonized_names.json"

_DEFAULT_MANDATORY = _SCHEMAS_DIR / "mandatory_fields.json"

MIN_WARN_GROUP_SIZE = 10


class KeyMapper:
    """
    Module 2: Key Harmonization.

    Loads the NCBI attribute XML cache and precomputed embeddings, then maps
    raw DataFrame column names to standard NCBI keys using exact synonym
    matching (Layer 1) with a sentence-transformers semantic fallback (Layer 2).

    Parameters
    ----------
    mandatory_path : str or Path, optional
        Override path to mandatory_fields.json. Defaults to schemas/mandatory_fields.json.
    """

    SEMANTIC_THRESHOLD = 0.75

    def __init__(self, mandatory_path=None):
        import biometaharmonizer.key_mapper as _this_module

        xml_cache  = _this_module._XML_CACHE
        emb_file   = _this_module._EMB_FILE
        names_file = _this_module._NAMES_FILE

        if not xml_cache.exists():
            raise RuntimeError(
                "NCBI attribute cache not found. "
                "Run scripts/build_ncbi_attribute_cache.py first."
            )

        self._exact = self._parse_xml_cache(xml_cache)

        with open(names_file, "r", encoding="utf-8") as fh:
            self._emb_names = json.load(fh)

        raw_emb = np.load(str(emb_file)).astype(np.float32)
        norms = np.linalg.norm(raw_emb, axis=1, keepdims=True)
        self._embeddings = raw_emb / np.where(norms == 0, 1, norms)
        self._embeddings = self._embeddings.astype(np.float32)

        self._model = None

        mandatory_path = Path(mandatory_path) if mandatory_path else _DEFAULT_MANDATORY
        if mandatory_path.exists():
            with open(mandatory_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.mandatory = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            self.mandatory = {"default": ["collection_date", "geo_loc_name", "isolate"]}

    @staticmethod
    def _parse_xml_cache(xml_path: Path) -> dict:
        """
        Parse ncbi_attributes.xml and return a dict mapping every
        synonym.lower() and harmonized_name.lower() to the canonical
        harmonized_name string.
        """
        exact: dict[str, str] = {}
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
        for attr in root.iter("Attribute"):
            hn_el = attr.find("HarmonizedName")
            if hn_el is None or not hn_el.text:
                continue
            hn = hn_el.text.strip()
            exact[hn.lower()] = hn
            for syn_el in attr.findall("Synonym"):
                if syn_el.text and syn_el.text.strip():
                    exact[syn_el.text.strip().lower()] = hn
        return exact

    def _resolve_column(self, col_lower: str):
        """
        Two-layer column resolution.

        Returns the canonical NCBI harmonized name, or None if no match
        meets the threshold.
        """
        if col_lower in self._exact:
            return self._exact[col_lower]

        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

        vec = self._model.encode([col_lower], normalize_embeddings=True)[0].astype(np.float32)
        sims = self._embeddings @ vec
        best_idx = int(np.argmax(sims))
        if sims[best_idx] >= self.SEMANTIC_THRESHOLD:
            return self._emb_names[best_idx]
        return None

    def map_columns(self, df, drop_sparse=5, drop_junk=True):
        """
        Harmonize column names, coalesce duplicates, clean junk/sparse
        columns, and report per-package mandatory field fill rates.

        Parameters
        ----------
        df : pd.DataFrame
            Raw ingested DataFrame.
        drop_sparse : int, default 5
            Drop columns with fewer than drop_sparse non-null values.
            Set to 0 to disable. Protected structural columns are never dropped.
        drop_junk : bool, default True
            Drop columns whose names look like person names or submitter
            artifacts. Protected structural columns are never dropped.
        """
        rename_map = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if col_lower in _PROTECTED_COLUMNS:
                continue
            resolved = self._resolve_column(col_lower)
            if resolved is not None:
                rename_map[col] = resolved

        df = df.rename(columns=rename_map)
        df = self._coalesce_duplicates(df)

        if drop_junk:
            df = self._drop_junk_columns(df)

        if drop_sparse and drop_sparse > 0:
            df = self._drop_sparse_columns(df, threshold=drop_sparse)

        self._warn_missing_mandatory(df)
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
        non_null = df.notna().sum()
        sparse = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and non_null[col] < threshold
        ]
        if sparse:
            print(f"[INFO] Dropping {len(sparse)} sparse columns (< {threshold} non-null values).")
            df = df.drop(columns=sparse)
        return df

    def _coalesce_duplicates(self, df):
        """
        When multiple raw columns rename to the same standard key,
        collapse them into one column using first-non-null coalescing.
        """
        if not df.columns.duplicated().any():
            return df

        seen = set()
        output_cols = {}
        duped = []

        for col in df.columns:
            if col not in seen:
                seen.add(col)
                block = df[col]
                if isinstance(block, pd.DataFrame):
                    duped.append(col)
                    coalesced = block.iloc[:, 0]
                    for i in range(1, block.shape[1]):
                        coalesced = coalesced.combine_first(block.iloc[:, i])
                    output_cols[col] = coalesced
                else:
                    output_cols[col] = block

        if duped:
            print(f"[INFO] Coalesced duplicate columns: {duped}")

        return pd.DataFrame(output_cols, index=df.index)

    def _warn_missing_mandatory(self, df):
        """
        For each ncbi_package group in df with >= MIN_WARN_GROUP_SIZE records,
        check fill rate of mandatory fields from mandatory_fields.json and
        warn when fill < 50%. Falls back to 'default' for unknown packages.
        Packages below MIN_WARN_GROUP_SIZE are silently skipped.
        """
        if "ncbi_package" not in df.columns:
            return

        for pkg, group in df.groupby("ncbi_package", dropna=False):
            n = len(group)
            if n < MIN_WARN_GROUP_SIZE:
                continue
            pkg_key  = pkg if pkg in self.mandatory else "default"
            required = self.mandatory.get(pkg_key, [])
            for field in required:
                if field not in df.columns:
                    print(f"[WARNING] [{pkg}] mandatory field '{field}' absent from dataset entirely.")
                else:
                    fill = group[field].notna().sum()
                    pct  = fill / n * 100
                    if pct < 50:
                        print(f"[WARNING] [{pkg}] mandatory field '{field}' fill rate: {fill}/{n} ({pct:.0f}%).")

    def get_parser_routing(self):
        """
        Return a dict of standard_key -> parser name for downstream dispatch.

        Parser names are derived from the unified.json convention and kept
        for backward compatibility with downstream pipeline code.
        """
        routing = {
            "collection_date":        "date_engine",
            "geo_loc_name":           "geo_engine",
            "lat_lon":                "string_cleaner",
            "host":                   "one_health_engine",
            "isolation_source":       "one_health_engine",
            "env_broad_scale":        "string_cleaner",
            "env_local_scale":        "string_cleaner",
            "env_medium":             "string_cleaner",
            "host_disease":           "string_cleaner",
            "isolate":                "string_cleaner",
            "sub_strain":             "string_cleaner",
            "serotype":               "string_cleaner",
            "serovar":                "string_cleaner",
            "host_age":               "string_cleaner",
            "host_sex":               "string_cleaner",
            "host_tissue_sampled":    "string_cleaner",
            "genotype":               "string_cleaner",
            "antimicrobial_resistance": "string_cleaner",
            "outbreak":               "string_cleaner",
            "collected_by":           "string_cleaner",
            "sequencing_method":      "string_cleaner",
            "assembly_method":        "string_cleaner",
            "culture_collection":     "string_cleaner",
            "samp_size":              "string_cleaner",
            "samp_mat_process":       "string_cleaner",
            "temp":                   "string_cleaner",
            "ph":                     "string_cleaner",
            "depth":                  "string_cleaner",
            "elev":                   "string_cleaner",
        }
        return routing
