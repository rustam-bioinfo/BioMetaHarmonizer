"""
Module 2: Key Harmonization.

Resolves raw DataFrame column names to NCBI standard keys using a two-layer approach:

  Layer 1 (authoritative): exact/synonym lookup from the NCBI BioSample attribute
      harmonization table (schemas/ncbi_attributes.xml), built by
      scripts/build_ncbi_attribute_cache.py.

  Layer 2 (semantic fallback): cosine similarity between a sentence-transformers
      embedding of the column name and precomputed embeddings of all NCBI
      harmonized names (schemas/ncbi_embeddings.npy).

      The model used for Layer 2 is configurable.  The default is the model
      that was used when building the cache (stored in ncbi_cache_meta.json).
      You can override it at KeyMapper construction time:

          mapper = KeyMapper(model="BAAI/bge-small-en-v1.5")

      IMPORTANT: if you override the model, the precomputed embeddings in
      ncbi_embeddings.npy will no longer match because they were produced by
      a different model.  Rebuild the cache first:

          python scripts/build_ncbi_attribute_cache.py --model BAAI/bge-small-en-v1.5

      Supported models (any sentence-transformers model works; these are tested):
          all-MiniLM-L6-v2         (default, 384-dim, fast, small)
          all-MiniLM-L12-v2        (384-dim, slightly better quality)
          all-mpnet-base-v2        (768-dim, higher quality, slower)
          BAAI/bge-small-en-v1.5   (384-dim, strong retrieval)
          BAAI/bge-base-en-v1.5    (768-dim, strong retrieval)
          intfloat/e5-small-v2     (384-dim, E5 family)
          intfloat/e5-base-v2      (768-dim, E5 family)

      Threshold: SEMANTIC_THRESHOLD = 0.75. Model is loaded lazily on first use.

The NCBI attribute cache must be built before using KeyMapper:
    python scripts/build_ncbi_attribute_cache.py [--model MODEL_NAME]

Mandatory field validation is per-package: each record's ncbi_package value is
looked up in mandatory_fields.json and fill rates are reported per package rather
than as a single global check. Packages with fewer than MIN_WARN_GROUP_SIZE records
are silently skipped to avoid noise from singleton or near-singleton submissions.

When multiple raw columns map to the same standard key, they are coalesced
(first non-null value wins).
"""

import contextlib
import importlib.resources
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


_PROTECTED_COLUMNS = frozenset([
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
])

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')
_EMAIL_RE       = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@contextlib.contextmanager
def _silence_stdio():
    """
    Context manager that redirects both Python-level sys.stdout/sys.stderr
    and the underlying OS-level file descriptors 1 and 2 to /dev/null.

    This suppresses all print() calls and low-level C-library writes
    (e.g. tqdm bars written directly to fd 2) that originate inside
    third-party library constructors such as SentenceTransformer().
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stdout_fd = os.dup(1)
    old_stderr_fd = os.dup(2)
    old_sys_stdout = sys.stdout
    old_sys_stderr = sys.stderr
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
        yield
    finally:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout = old_sys_stdout
        sys.stderr = old_sys_stderr
        os.dup2(old_stdout_fd, 1)
        os.dup2(old_stderr_fd, 2)
        os.close(old_stdout_fd)
        os.close(old_stderr_fd)
        os.close(devnull_fd)


def _schemas_dir() -> Path:
    try:
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


_SCHEMAS_DIR  = _schemas_dir()
_XML_CACHE    = _SCHEMAS_DIR / "ncbi_attributes.xml"
_EMB_FILE     = _SCHEMAS_DIR / "ncbi_embeddings.npy"
_NAMES_FILE   = _SCHEMAS_DIR / "ncbi_harmonized_names.json"
_META_FILE    = _SCHEMAS_DIR / "ncbi_cache_meta.json"

_DEFAULT_MANDATORY = _SCHEMAS_DIR / "mandatory_fields.json"

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


def _cached_model_name() -> str:
    if _META_FILE.exists():
        try:
            with open(_META_FILE, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            model = meta.get("model", _DEFAULT_MODEL)
            if model:
                return model
        except (json.JSONDecodeError, OSError):
            pass
    return _DEFAULT_MODEL


MIN_WARN_GROUP_SIZE = 10


class KeyMapper:
    """
    Module 2: Key Harmonization.

    Loads the NCBI attribute XML cache and precomputed embeddings, then maps
    raw DataFrame column names to standard NCBI keys using exact synonym
    matching (Layer 1) with a sentence-transformers semantic fallback (Layer 2).

    Layer 2 encodes all unresolved column names in a single model.encode()
    call (one batch) rather than one call per column, so there is at most
    one progress bar regardless of how many columns need semantic resolution.

    Parameters
    ----------
    mandatory_path : str or Path, optional
        Override path to mandatory_fields.json.
    model : str, optional
        sentence-transformers model name for Layer 2. Defaults to the model
        recorded in ncbi_cache_meta.json.
    threshold : float, optional
        Cosine similarity threshold for Layer 2 acceptance (default 0.75).
    """

    SEMANTIC_THRESHOLD = 0.75

    def __init__(self, mandatory_path=None, model: str = None, threshold: float = None):
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

        self._model_name: str = model if model is not None else _cached_model_name()
        self._model = None  # loaded lazily on first semantic lookup

        if threshold is not None:
            self.SEMANTIC_THRESHOLD = float(threshold)

        mandatory_path = Path(mandatory_path) if mandatory_path else _DEFAULT_MANDATORY
        if mandatory_path.exists():
            with open(mandatory_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.mandatory = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            self.mandatory = {"default": ["collection_date", "geo_loc_name", "isolate"]}

    @staticmethod
    def _parse_xml_cache(xml_path: Path) -> dict:
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

    def _load_model(self):
        """
        Load the sentence-transformers model lazily on first use.

        All stdout/stderr output produced during SentenceTransformer.__init__
        is suppressed via _silence_stdio(), which redirects both Python-level
        streams and the underlying OS file descriptors to /dev/null. This
        silences print()-based warnings from huggingface_hub, tqdm weight-
        loading bars from mlx/jax, and the BertModel LOAD REPORT block from
        sentence-transformers >= 3.x -- for any model, not just the default.

        Logger levels for noisy third-party loggers are also set to WARNING
        so that HTTP HEAD request lines logged at INFO do not reappear on
        subsequent encode() calls.
        """
        for noisy in (
            "httpx",
            "huggingface_hub",
            "huggingface_hub.utils._http",
            "sentence_transformers",
            "sentence_transformers.base.model",
            "datasets",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformers model: %s", self._model_name)
        with _silence_stdio():
            self._model = SentenceTransformer(self._model_name)

    def _resolve_columns_batch(self, col_lowers: list) -> dict:
        """
        Resolve a list of lowercased column names to NCBI standard keys.

        Layer 1 (exact/synonym lookup) is applied first for every name.
        All names not resolved by Layer 1 are encoded together in a single
        model.encode() call, then matched against precomputed embeddings
        via a single matrix multiply.
        """
        resolved = {}
        unresolved = []

        for col in col_lowers:
            if col in self._exact:
                resolved[col] = self._exact[col]
            else:
                unresolved.append(col)

        if not unresolved:
            return resolved

        if self._model is None:
            self._load_model()

        vecs = self._model.encode(
            unresolved,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype(np.float32)

        sims = vecs @ self._embeddings.T
        best_indices = np.argmax(sims, axis=1)
        best_scores  = sims[np.arange(len(unresolved)), best_indices]

        for col, idx, score in zip(unresolved, best_indices, best_scores):
            if score >= self.SEMANTIC_THRESHOLD:
                resolved[col] = self._emb_names[int(idx)]

        return resolved

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
        to_resolve = [
            (col, col.lower().strip())
            for col in df.columns
            if col.lower().strip() not in _PROTECTED_COLUMNS
        ]

        if to_resolve:
            col_lowers = [col_lower for _, col_lower in to_resolve]
            batch_result = self._resolve_columns_batch(col_lowers)
            rename_map = {
                col: batch_result[col_lower]
                for col, col_lower in to_resolve
                if col_lower in batch_result
            }
        else:
            rename_map = {}

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
