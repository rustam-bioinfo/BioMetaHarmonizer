"""
Module 1: Universal Data Ingestion.

Fetches NCBI BioSample metadata for lists of BioSample IDs or assembly accessions.
BioProject accession is resolved from NCBI assembly summary flat files, which are
downloaded once to a configurable cache directory and refreshed every 7 days.

This module now defines the canonical fixed output schema for the entire tool.
Every record is initialized with all predefined columns, so downstream steps only
fill values in-place and never create new columns. Any attribute that does not
resolve to a known final output column is preserved in `_extra_attributes` as JSON.

Entrez rate limits:
  Without API key: 3 requests/second (1 batch = 1 request)
  With API key:   10 requests/second
Register a free API key at https://www.ncbi.nlm.nih.gov/account/ and pass it to
set_api_key() or ingest(api_key=...).

Working directory note (Colab):
  Assembly summary flat files (~100 MB each) are cached in CACHE_DIR, which defaults
  to ~/.biometaharmonizer/cache/. In Colab, override with set_cache_dir("/content")
  if you want them in the working directory.
"""

import functools
import http.client
import importlib.resources
import json
import logging
import re
import time
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from Bio import Entrez

from biometaharmonizer.synonyms import build_synonym_lookup


logger = logging.getLogger(__name__)


_DEFAULT_EMAIL = "your@email.com"
ENTREZ_EMAIL = _DEFAULT_EMAIL
ENTREZ_API_KEY = None
CACHE_DIR = Path.home() / ".biometaharmonizer" / "cache"

ASSEMBLY_SUMMARY_REFSEQ = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt"
ASSEMBLY_SUMMARY_GENBANK = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt"

_BATCH_SIZE = 200
_ESEARCH_BATCH = 100
_MAX_RETRIES = 3
_RETRY_BASE_S = 2
_RETRY_MAX_S = 30
_CACHE_TTL_DAYS = 7

# Prefixes that identify native BioSample accessions (INSDC standard).
_BIOSAMPLE_PREFIXES = ("SAMN", "SAME", "SAMD")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_TRANSIENT_EXCEPTIONS = (
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionError,
    TimeoutError,
    OSError,
)

_NULL_PATTERNS = re.compile(
    r"^(?:-+|\.+|n/?a|na|nd|nr|ns|nt|none|null|nil|"
    r"missing|misssing|missng|mising|"
    r"unknown|unkown|unknwon|unknow|"
    r"not\s+provided|not\s+collected|not\s+applicable|not\s+available|"
    r"not\s+determined|not\s+recorded|not\s+reported|not\s+known|"
    r"not\s+given|not\s+stated|not\s+specified|"
    r"not\s+done|not\s+tested|not\s+sequenced|not\s+typed|"
    r"unavailable|unspecified|undetermined|unidentified|"
    r"restricted|restricted\s+access|withheld|confidential|"
    r"tbd|tba|"
    r"missing\s*:.*|not\s+applicable\s*:.*|data\s+agreement\s+established\s+pre-?2023)$",
    re.IGNORECASE,
)


def _normalize_null(value):
    if value is None:
        return None
    if pd.isna(value):
        return None
    value = str(value).strip()
    if not value:
        return None
    if _NULL_PATTERNS.match(value):
        return None
    return value


def _schemas_dir() -> Path:
    try:
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


def _load_final_schema() -> list:
    return [
        "biosample_accession",
        "biosample_id",
        "sra_accession",
        "bioproject_accession",
        "assembly_accession_refseq",
        "assembly_accession_genbank",
        "sample_name_id",
        "taxonomy_id",
        "taxonomy_name",
        "organism_name",
        "collection_date",
        "collection_date_range",
        "geo_loc_name",
        "lat_lon",
        "geo_country",
        "geo_region",
        "geo_locality",
        "geo_iso3166",
        "geo_sea_ocean",
        "geo_loc_raw",
        "host",
        "host_disease",
        "host_age",
        "host_sex",
        "host_tissue_sampled",
        "isolation_source",
        "sample_type",
        "one_health_category",
        "isolate",
        "strain",
        "sub_strain",
        "serotype",
        "serovar",
        "genotype",
        "culture_collection",
        "outbreak",
        "env_broad_scale",
        "env_local_scale",
        "env_medium",
        "sequencing_method",
        "assembly_method",
        "collected_by",
        "ncbi_package",
        "submission_date",
        "last_update",
        "publication_date",
        "access",
        "status",
        "status_date",
        "title",
        "description_comment",
        "_extra_attributes",
    ]


BIOSAMPLE_SCHEMA = _load_final_schema()
BIOSAMPLE_SCHEMA_SET = set(BIOSAMPLE_SCHEMA)


def set_email(email: str) -> None:
    global ENTREZ_EMAIL
    email = str(email).strip()
    if not _EMAIL_RE.match(email):
        raise ValueError(
            f"Invalid email address: {email!r}. "
            "NCBI requires a valid contact email for all Entrez API calls."
        )
    ENTREZ_EMAIL = email
    Entrez.email = email


def set_api_key(key: str) -> None:
    global ENTREZ_API_KEY
    ENTREZ_API_KEY = key
    Entrez.api_key = key


def set_cache_dir(path) -> None:
    global CACHE_DIR
    CACHE_DIR = Path(path)
    _read_assembly_summary_cached.cache_clear()


@functools.lru_cache(maxsize=2)
def _read_assembly_summary_cached(path_str: str, mtime: float) -> pd.DataFrame:
    """Read and cache one assembly summary file.  Keyed on path + mtime."""
    return _read_assembly_summary_uncached(Path(path_str))


def _read_assembly_summary_uncached(cache_path: Path, extra_cols: list = None) -> pd.DataFrame:
    needed = ["assembly_accession", "biosample", "bioproject"] + (extra_cols or [])

    with open(cache_path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i == 1:
                raw_first_col = line.split("\t")[0].strip()
                break
        else:
            raise ValueError(f"Assembly summary file appears to be empty: {cache_path}")

    df = pd.read_csv(
        cache_path,
        sep="\t",
        skiprows=1,
        low_memory=False,
        dtype=str,
    )
    df = df.rename(columns={raw_first_col: "assembly_accession"})
    available = [c for c in needed if c in df.columns]
    return df[available]


def _read_assembly_summary(cache_path: Path, extra_cols: list = None) -> pd.DataFrame:
    if extra_cols:
        return _read_assembly_summary_uncached(cache_path, extra_cols=extra_cols)
    mtime = cache_path.stat().st_mtime if cache_path.exists() else 0.0
    return _read_assembly_summary_cached(str(cache_path), mtime)


def ingest(source, email: str = None, api_key: str = None, cache_dir=None) -> pd.DataFrame:
    resolved_email = email or (None if ENTREZ_EMAIL == _DEFAULT_EMAIL else ENTREZ_EMAIL)
    if not resolved_email:
        raise ValueError(
            "An email address is required for NCBI Entrez API calls. "
            "Pass email='your@email.com' to ingest() or call set_email() beforehand."
        )
    set_email(resolved_email)

    if api_key is not None:
        set_api_key(api_key)
    if cache_dir is not None:
        set_cache_dir(cache_dir)

    Entrez.email = ENTREZ_EMAIL
    Entrez.api_key = ENTREZ_API_KEY

    ids = _load_ids(source)

    if not ids:
        logger.warning(
            "ingest() called with an empty accession list. Returning empty DataFrame."
        )
        return pd.DataFrame(columns=BIOSAMPLE_SCHEMA)

    ids = _deduplicate(ids)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        logger.warning("%d unrecognized IDs skipped: %s", len(unrecognized), unrecognized[:5])

    _ensure_assembly_summaries()

    n_gcx_input = len(gcx)
    unresolved_gcx = []

    if gcx:
        logger.info("Resolving %d assembly accessions to BioSample IDs...", len(gcx))
        resolved, unresolved_gcx = _resolve_assembly_to_biosample(gcx)
        samn = list(set(samn + resolved))

    if not samn:
        reasons = []
        if unrecognized:
            reasons.append(
                f"{len(unrecognized)} unrecognized IDs (expected SAMN/SAME/SAMD/GCF/GCA prefixes)"
            )
        if gcx and unresolved_gcx:
            reasons.append(
                f"{len(unresolved_gcx)} assembly accessions not found in the local assembly index "
                f"(cache dir: {CACHE_DIR}; delete cached assembly_summary_*.txt to force refresh)"
            )
        if not reasons:
            reasons.append(
                "no BioSample accessions remained after input classification and assembly-to-BioSample resolution"
            )
        raise ValueError("No valid BioSample IDs could be resolved. Reasons: " + "; ".join(reasons))

    synonym_lookup = build_synonym_lookup()
    logger.info("Fetching metadata for %d BioSample accessions...", len(samn))
    df = _fetch_biosample_metadata(samn, synonym_lookup=synonym_lookup)

    biosample_set = set(df["biosample_accession"].dropna())

    logger.info("Resolving BioProject and assembly accessions from assembly index...")
    assembly_map = _resolve_biosample_to_assembly(biosample_set)

    if assembly_map:
        df["bioproject_accession"] = df["biosample_accession"].map(
            lambda x: assembly_map.get(x, {}).get("bioproject")
        ).fillna(df["bioproject_accession"])

        df["assembly_accession_refseq"] = df["biosample_accession"].map(
            lambda x: assembly_map.get(x, {}).get("refseq")
        )
        df["assembly_accession_genbank"] = df["biosample_accession"].map(
            lambda x: assembly_map.get(x, {}).get("genbank")
        )

        filled_bp = df["bioproject_accession"].notna().sum()
        filled_rs = df["assembly_accession_refseq"].notna().sum()
        filled_gb = df["assembly_accession_genbank"].notna().sum()
        logger.info(
            "Resolved: bioproject=%d  refseq=%d  genbank=%d  (of %d records)",
            filled_bp, filled_rs, filled_gb, len(df),
        )
    else:
        logger.warning("No assembly index hits found for this dataset.")

    logger.info("=" * 60)
    logger.info("INGEST SUMMARY")
    logger.info("  Input IDs provided  : %d", len(ids))
    if n_gcx_input:
        logger.info("  Assembly accessions : %d", n_gcx_input)
        logger.info(
            "    Resolved to BioSample : %d", n_gcx_input - len(unresolved_gcx)
        )
        if unresolved_gcx:
            logger.warning(
                "    NOT resolved (absent from assembly index or suppressed): %d",
                len(unresolved_gcx),
            )
            logger.warning("    Unresolved: %s", unresolved_gcx)
    logger.info("  Records in output   : %d", len(df))
    logger.info("  bioproject_accession filled : %d / %d",
                df["bioproject_accession"].notna().sum(), len(df))
    logger.info("  assembly_accession_refseq   filled : %d / %d",
                df["assembly_accession_refseq"].notna().sum(), len(df))
    logger.info("  assembly_accession_genbank  filled : %d / %d",
                df["assembly_accession_genbank"].notna().sum(), len(df))
    if unrecognized:
        logger.warning("  Unrecognized input IDs skipped: %d -- %s",
                       len(unrecognized), unrecognized[:10])
    logger.info("=" * 60)

    return df.reindex(columns=BIOSAMPLE_SCHEMA)


def _load_ids(source) -> list:
    if isinstance(source, list):
        return [s.strip() for s in source if s.strip()]
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with open(path, "r") as fh:
        return [line.strip() for line in fh if line.strip()]


def _deduplicate(ids: list) -> list:
    seen = set()
    unique = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    n_dupes = len(ids) - len(unique)
    if n_dupes:
        logger.info("Removed %d duplicate input IDs.", n_dupes)
    return unique


def _classify_ids(ids: list) -> tuple:
    gcx, samn, unrecognized = [], [], []
    for i in ids:
        i_upper = i.upper()
        if i_upper.startswith(("GCF_", "GCA_")):
            gcx.append(i)
        elif i_upper.startswith(("SAMN", "SAME", "SAMD")):
            samn.append(i)
        else:
            unrecognized.append(i)
    return gcx, samn, unrecognized


def _ensure_assembly_summaries() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url, label in [
        (ASSEMBLY_SUMMARY_REFSEQ, "refseq"),
        (ASSEMBLY_SUMMARY_GENBANK, "genbank"),
    ]:
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"

        if cache_path.exists():
            age_days = (time.time() - cache_path.stat().st_mtime) / (24 * 3600)
            if age_days > _CACHE_TTL_DAYS:
                logger.info(
                    "Assembly index (%s) is %d days old -- refreshing.",
                    label, int(age_days),
                )
                try:
                    cache_path.unlink()
                except OSError:
                    pass
                _read_assembly_summary_cached.cache_clear()
            else:
                logger.debug("Assembly index cache OK: %s (%.1f days old)", label, age_days)

        if not cache_path.exists():
            logger.info(
                "Fetching NCBI assembly index (%s) -- this runs once and may take a moment...",
                label,
            )
            _download_file(url, cache_path)
            logger.info("Assembly index (%s) ready.", label)


def _resolve_assembly_to_biosample(gcx_ids: list) -> tuple:
    resolved = []
    gcx_set = set(gcx_ids)

    for label in ["refseq", "genbank"]:
        if not gcx_set:
            break
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            continue
        try:
            df = _read_assembly_summary(cache_path)
            hits = df[
                df["assembly_accession"].isin(gcx_set)
                & df["biosample"].notna()
            ]
            for acc, biosample in zip(hits["assembly_accession"], hits["biosample"]):
                if acc in gcx_set:
                    resolved.append(biosample)
                    gcx_set.discard(acc)
        except Exception as exc:
            logger.warning(
                "Could not read assembly index (%s) during accession resolution: %s",
                label, exc,
            )

    unresolved = list(gcx_set)
    if unresolved:
        logger.warning(
            "%d assembly accessions not found in either index (suppressed or absent): %s",
            len(unresolved), unresolved[:5],
        )
    return resolved, unresolved


def _resolve_biosample_to_assembly(biosample_ids: set) -> dict:
    lookup: dict = {}

    for label in ["refseq", "genbank"]:
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            continue
        try:
            df = _read_assembly_summary(cache_path)
            hits = df[
                df["biosample"].isin(biosample_ids)
                & df["assembly_accession"].notna()
            ].copy()

            if hits.empty:
                continue

            hits["_is_refseq"] = hits["assembly_accession"].str.startswith("GCF_")

            bp_series = (
                hits[hits["bioproject"].notna()]
                .groupby("biosample")["bioproject"]
                .first()
            )
            refseq_series = (
                hits[hits["_is_refseq"]]
                .groupby("biosample")["assembly_accession"]
                .first()
            )
            genbank_series = (
                hits[~hits["_is_refseq"]]
                .groupby("biosample")["assembly_accession"]
                .first()
            )

            for bs in hits["biosample"].unique():
                if bs not in lookup:
                    lookup[bs] = {"bioproject": None, "refseq": None, "genbank": None}
                if lookup[bs]["bioproject"] is None and bs in bp_series.index:
                    lookup[bs]["bioproject"] = bp_series[bs]
                if lookup[bs]["refseq"] is None and bs in refseq_series.index:
                    lookup[bs]["refseq"] = refseq_series[bs]
                if lookup[bs]["genbank"] is None and bs in genbank_series.index:
                    lookup[bs]["genbank"] = genbank_series[bs]

        except Exception as exc:
            logger.warning(
                "Could not read assembly index (%s) for resolution: %s", label, exc
            )

    return lookup


def _fetch_biosample_metadata(samn_ids: list, synonym_lookup: dict = None) -> pd.DataFrame:
    """Fetch BioSample XML records for a list of accessions.

    For SAMN/SAME/SAMD accessions the pipeline is:
      esearch(term=batch[Accession] OR ..., usehistory=y)  -- one call per batch
      efetch(WebEnv=..., query_key=..., retstart=0, retmax=batch_size) -- one call per batch

    Each batch produces its own fresh WebEnv+query_key that covers exactly
    the accessions in that batch.  This avoids the cross-batch accumulation
    bug where the last query_key only indexes the last batch.
    """
    inter_batch_sleep = 0.12 if ENTREZ_API_KEY else 0.34
    requested_set = set(samn_ids)

    direct_ids = [a for a in samn_ids if a.upper().startswith(_BIOSAMPLE_PREFIXES)]
    needs_resolution = [a for a in samn_ids if not a.upper().startswith(_BIOSAMPLE_PREFIXES)]

    records = []
    total = len(direct_ids)
    n_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE

    # --- Path 1: SAMN/SAME/SAMD -- esearch(usehistory=y) + efetch per batch ---
    for batch_i, start in enumerate(range(0, total, _BATCH_SIZE)):
        batch = direct_ids[start:start + _BATCH_SIZE]
        term = " OR ".join(f"{acc}[Accession]" for acc in batch)

        web_env = None
        query_key = None
        found = 0

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                handle = Entrez.esearch(
                    db="biosample",
                    term=term,
                    retmax=0,
                    usehistory="y",
                )
                result = Entrez.read(handle)
                handle.close()
                web_env = result["WebEnv"]
                query_key = result["QueryKey"]
                found = int(result.get("Count", 0))
                break
            except _TRANSIENT_EXCEPTIONS as exc:
                wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
                logger.warning(
                    "esearch attempt %d/%d failed (batch %d/%d): %s. Retrying in %ds...",
                    attempt, _MAX_RETRIES, batch_i + 1, n_batches, exc, wait,
                )
                time.sleep(wait)
        else:
            logger.error(
                "esearch failed after %d retries for batch %d/%d (%d accessions skipped).",
                _MAX_RETRIES, batch_i + 1, n_batches, len(batch),
            )
            if batch_i < n_batches - 1:
                time.sleep(inter_batch_sleep)
            continue

        if found == 0:
            logger.warning(
                "esearch returned 0 results for batch %d/%d -- these accessions may be "
                "suppressed or invalid: %s",
                batch_i + 1, n_batches, batch[:3],
            )
            if batch_i < n_batches - 1:
                time.sleep(inter_batch_sleep)
            continue

        batch_records = _fetch_batch_via_history(
            web_env, query_key, retstart=0, retmax=found,
            synonym_lookup=synonym_lookup,
        )

        if batch_records is None:
            logger.error(
                "efetch failed for batch %d/%d after %d retries (%d records excluded).",
                batch_i + 1, n_batches, _MAX_RETRIES, found,
            )
        else:
            for rec in batch_records:
                acc = rec.get("biosample_accession") or ""
                if acc in requested_set:
                    records.append(rec)
                else:
                    logger.warning(
                        "Unexpected record returned by NCBI and discarded: "
                        "biosample_accession=%r (not in requested input set)", acc,
                    )

        fetched_so_far = min(start + _BATCH_SIZE, total)
        logger.info(
            "Fetched %d / %d (%d/%d batches)",
            fetched_so_far, total, batch_i + 1, n_batches,
        )
        if batch_i < n_batches - 1:
            time.sleep(inter_batch_sleep)

    # --- Path 2: non-standard accessions -- legacy esearch+esummary+efetch ---
    if needs_resolution:
        logger.info(
            "Resolving %d non-BioSample accessions to NCBI UIDs via esearch...",
            len(needs_resolution),
        )
        acc_to_uid = _resolve_accessions_to_uids(needs_resolution)
        unresolved = [a for a in needs_resolution if a not in acc_to_uid]
        if unresolved:
            logger.warning(
                "%d accessions could not be resolved to UIDs and will be skipped: %s",
                len(unresolved), unresolved[:5],
            )

        uid_list = list(acc_to_uid.values())
        if uid_list:
            leg_total = len(uid_list)
            leg_batches = (leg_total + _BATCH_SIZE - 1) // _BATCH_SIZE
            logger.info("Fetching metadata for %d resolved UIDs (legacy path)...", leg_total)
            for batch_i, start in enumerate(range(0, leg_total, _BATCH_SIZE)):
                uid_batch = uid_list[start:start + _BATCH_SIZE]
                batch_records = _fetch_batch_with_retry(uid_batch, synonym_lookup=synonym_lookup)
                if batch_records is None:
                    logger.error(
                        "Legacy batch %d/%d failed after %d retries.",
                        batch_i + 1, leg_batches, _MAX_RETRIES,
                    )
                else:
                    for rec in batch_records:
                        acc = rec.get("biosample_accession") or ""
                        if acc in requested_set:
                            records.append(rec)
                if batch_i < leg_batches - 1:
                    time.sleep(inter_batch_sleep)

    return pd.DataFrame(records).reindex(columns=BIOSAMPLE_SCHEMA)


def _resolve_accessions_to_uids(accessions: list) -> dict:
    """Resolve non-BioSample accessions to numeric UIDs via esearch + esummary."""
    inter_req_sleep = 0.12 if ENTREZ_API_KEY else 0.34
    acc_to_uid = {}

    for start in range(0, len(accessions), _ESEARCH_BATCH):
        batch = accessions[start:start + _ESEARCH_BATCH]
        term = " OR ".join(f"{acc}[Accession]" for acc in batch)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                handle = Entrez.esearch(
                    db="biosample",
                    term=term,
                    retmax=len(batch),
                    usehistory="n",
                )
                result = Entrez.read(handle)
                handle.close()
                break
            except _TRANSIENT_EXCEPTIONS as exc:
                wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
                logger.warning(
                    "esearch attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
        else:
            logger.warning(
                "esearch failed for batch starting at index %d. These accessions will be skipped.",
                start,
            )
            continue

        uids = result.get("IdList", [])
        if not uids:
            continue

        for uid_start in range(0, len(uids), _ESEARCH_BATCH):
            uid_batch = uids[uid_start:uid_start + _ESEARCH_BATCH]
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    sum_handle = Entrez.esummary(db="biosample", id=",".join(uid_batch))
                    summaries = Entrez.read(sum_handle)
                    sum_handle.close()
                    break
                except _TRANSIENT_EXCEPTIONS as exc:
                    wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
                    logger.warning(
                        "esummary attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt, _MAX_RETRIES, exc, wait,
                    )
                    time.sleep(wait)
            else:
                continue

            for doc in summaries["DocumentSummarySet"]["DocumentSummary"]:
                uid = doc.attributes.get("uid", "")
                acc = doc.get("Accession", "")
                if acc and uid:
                    acc_to_uid[acc] = uid

            time.sleep(inter_req_sleep)

        time.sleep(inter_req_sleep)

    return acc_to_uid


def _fetch_batch_via_history(
    web_env: str,
    query_key: str,
    retstart: int,
    retmax: int,
    synonym_lookup: dict = None,
):
    """Fetch one page of BioSample records from an Entrez History slot."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            handle = Entrez.efetch(
                db="biosample",
                WebEnv=web_env,
                query_key=query_key,
                retstart=retstart,
                retmax=retmax,
                rettype="full",
                retmode="xml",
            )
            raw = handle.read()
            handle.close()
            return _parse_biosample_xml(raw, synonym_lookup=synonym_lookup)
        except ET.ParseError as exc:
            logger.error("XML ParseError on efetch (retstart=%d): %s", retstart, exc)
            return []
        except _TRANSIENT_EXCEPTIONS as exc:
            wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
            logger.warning(
                "efetch attempt %d/%d failed (retstart=%d, transient): %s. "
                "Retrying in %ds...",
                attempt, _MAX_RETRIES, retstart, exc, wait,
            )
            time.sleep(wait)
    logger.error(
        "efetch failed after %d retries (retstart=%d, retmax=%d).",
        _MAX_RETRIES, retstart, retmax,
    )
    return None


def _fetch_batch_with_retry(uid_batch: list, synonym_lookup: dict = None):
    """Fetch BioSample records by explicit numeric UID list (legacy path)."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            handle = Entrez.efetch(
                db="biosample",
                id=",".join(uid_batch),
                rettype="full",
                retmode="xml",
            )
            raw = handle.read()
            handle.close()
            return _parse_biosample_xml(raw, synonym_lookup=synonym_lookup)
        except ET.ParseError as exc:
            logger.error(
                "XML ParseError on efetch batch (malformed/truncated response): %s", exc
            )
            return []
        except _TRANSIENT_EXCEPTIONS as exc:
            wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
            logger.warning(
                "Batch fetch attempt %d/%d failed (transient): %s. Retrying in %ds...",
                attempt, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    logger.error(
        "efetch failed after %d retries for %d UIDs.", _MAX_RETRIES, len(uid_batch)
    )
    return None


def _parse_biosample_xml(xml_bytes: bytes, synonym_lookup: dict = None) -> list:
    if not xml_bytes or not xml_bytes.strip():
        logger.warning("Received empty XML response from NCBI efetch -- skipping batch.")
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.error("XML ParseError from NCBI response (truncated?): %s", exc)
        logger.debug("Partial XML content (first 200 bytes): %r", xml_bytes[:200])
        raise

    records = []
    for sample in root.findall(".//BioSample"):
        record = dict.fromkeys(BIOSAMPLE_SCHEMA, None)

        record["biosample_accession"] = _normalize_null(sample.get("accession"))
        record["biosample_id"] = _normalize_null(sample.get("id"))
        record["submission_date"] = _normalize_null(sample.get("submission_date"))
        record["last_update"] = _normalize_null(sample.get("last_update"))
        record["publication_date"] = _normalize_null(sample.get("publication_date"))
        record["access"] = _normalize_null(sample.get("access"))

        for db_id in sample.findall(".//Id"):
            db = (db_id.get("db", "") or "").strip()
            label = (db_id.get("db_label", "") or "").strip()
            val = _normalize_null(db_id.text)
            if db == "SRA":
                record["sra_accession"] = val
            elif db == "BioProject" and val:
                record["bioproject_accession"] = val
            elif label == "Sample name" and val:
                record["sample_name_id"] = val

        title_el = sample.find(".//Description/Title")
        record["title"] = _normalize_null(title_el.text if title_el is not None else None)

        comment_el = sample.find(".//Description/Comment/Paragraph")
        record["description_comment"] = _normalize_null(
            comment_el.text if comment_el is not None else None
        )

        organism = sample.find(".//Organism")
        if organism is not None:
            record["taxonomy_id"] = _normalize_null(organism.get("taxonomy_id"))
            record["taxonomy_name"] = _normalize_null(organism.get("taxonomy_name"))
            org_name_el = organism.find(".//OrganismName")
            if org_name_el is not None and org_name_el.text:
                record["organism_name"] = _normalize_null(org_name_el.text)
            else:
                record["organism_name"] = _normalize_null(organism.get("taxonomy_name"))

        package_el = sample.find(".//Package")
        record["ncbi_package"] = _normalize_null(
            package_el.text if package_el is not None else None
        )

        status_el = sample.find(".//Status")
        if status_el is not None:
            record["status"] = _normalize_null(status_el.get("status"))
            record["status_date"] = _normalize_null(status_el.get("when"))

        extras = {}
        for attr in sample.findall(".//Attribute"):
            hn = (attr.get("harmonized_name") or "").strip()
            an = (attr.get("attribute_name") or "").strip()
            raw_key = hn or an or "unknown"
            val = _normalize_null(attr.text)

            resolved = None
            if hn and hn in BIOSAMPLE_SCHEMA_SET:
                resolved = hn
            elif synonym_lookup is not None and hn and hn.lower() in synonym_lookup:
                candidate = synonym_lookup[hn.lower()]
                if candidate in BIOSAMPLE_SCHEMA_SET:
                    resolved = candidate
                else:
                    raw_key = candidate
            elif synonym_lookup is not None and an and an.lower() in synonym_lookup:
                candidate = synonym_lookup[an.lower()]
                if candidate in BIOSAMPLE_SCHEMA_SET:
                    resolved = candidate
                else:
                    raw_key = candidate

            if resolved is not None:
                if record.get(resolved) is None:
                    record[resolved] = val
                elif val is not None:
                    existing = extras.get(raw_key)
                    extras[raw_key] = f"{existing}|{val}" if existing else val
                    logger.debug(
                        "Attribute collision on '%s' (biosample=%s): primary value kept, "
                        "duplicate stored in _extra_attributes.",
                        resolved, record.get("biosample_accession"),
                    )
            elif val is not None:
                existing = extras.get(raw_key)
                extras[raw_key] = f"{existing}|{val}" if existing else val

        owner_el = sample.find(".//Owner/Name")
        owner_name = _normalize_null(owner_el.text if owner_el is not None else None)
        if owner_name:
            if record.get("collected_by") is None:
                record["collected_by"] = owner_name
            else:
                existing = extras.get("submission_owner")
                extras["submission_owner"] = (
                    f"{existing}|{owner_name}" if existing else owner_name
                )

        contact_el = sample.find(".//Owner/Contacts/Contact")
        if contact_el is not None:
            name_parts = []
            for tag in ["First", "Middle", "Last"]:
                part_el = contact_el.find(tag)
                part_val = _normalize_null(part_el.text if part_el is not None else None)
                if part_val:
                    name_parts.append(part_val)
            if name_parts:
                contact_name = " ".join(name_parts)
                existing = extras.get("submission_contact")
                extras["submission_contact"] = (
                    f"{existing}|{contact_name}" if existing else contact_name
                )

        record["_extra_attributes"] = json.dumps(extras) if extras else None
        records.append(record)

    return records


def _download_file(url: str, dest_path: Path) -> None:
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    try:
        with requests.get(url, stream=True) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    fh.write(chunk)
        tmp_path.rename(dest_path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
