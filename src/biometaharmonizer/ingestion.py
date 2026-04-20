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

import importlib.resources
import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from Bio import Entrez

from biometaharmonizer.synonyms import build_synonym_lookup


logger = logging.getLogger(__name__)


# --- Module-level configuration -------------------------------------------

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


def _schemas_dir() -> Path:
    try:
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


def _load_final_schema() -> list:
    """
    Build the canonical ordered final output schema.

    Order:
      1. Structural BioSample fields extracted directly from XML
      2. All standard_key values from unified.json, in schema order
      3. Downstream in-place enrichment columns
      4. Assembly accession columns resolved from flat files
      5. _extra_attributes last
    """
    structural = [
        "biosample_accession", "biosample_id", "sra_accession",
        "bioproject_accession", "sample_name_id", "submission_date",
        "last_update", "publication_date", "access", "status",
        "status_date", "title", "description_comment", "ncbi_package",
        "taxonomy_id", "taxonomy_name", "organism_name",
    ]

    schemas_dir = _schemas_dir()
    unified_path = schemas_dir / "unified.json"
    standard_keys = []
    if unified_path.exists():
        with open(unified_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        standard_keys = [field["standard_key"] for field in schema.get("fields", [])]

    downstream = [
        "collection_date_range",
        "geo_country", "geo_region", "geo_locality",
        "geo_iso3166", "geo_sea_ocean", "geo_loc_raw",
        "one_health_category",
        "assembly_accession_refseq",
        "assembly_accession_genbank",
    ]

    final = []
    for col in structural + standard_keys + downstream + ["_extra_attributes"]:
        if col not in final:
            final.append(col)
    return final


BIOSAMPLE_SCHEMA = _load_final_schema()
BIOSAMPLE_SCHEMA_SET = set(BIOSAMPLE_SCHEMA)


def set_email(email: str) -> None:
    global ENTREZ_EMAIL
    ENTREZ_EMAIL = email
    Entrez.email = email


def set_api_key(key: str) -> None:
    global ENTREZ_API_KEY
    ENTREZ_API_KEY = key
    Entrez.api_key = key


def set_cache_dir(path) -> None:
    global CACHE_DIR
    CACHE_DIR = Path(path)


def ingest(source, api_key: str = None, cache_dir=None) -> pd.DataFrame:
    if api_key is not None:
        set_api_key(api_key)
    if cache_dir is not None:
        set_cache_dir(cache_dir)

    if ENTREZ_EMAIL == _DEFAULT_EMAIL:
        raise ValueError(
            "A valid e-mail address is required by NCBI for all Entrez API calls. "
            "Call set_email('your@real.email') before calling ingest(), "
            "or pass email via the CLI --email flag."
        )

    Entrez.email = ENTREZ_EMAIL
    Entrez.api_key = ENTREZ_API_KEY

    ids = _load_ids(source)
    ids = _deduplicate(ids)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        logger.warning("%d unrecognized IDs skipped: %s", len(unrecognized), unrecognized[:5])

    # Always ensure assembly summaries are cached so that BioProject accessions
    # and assembly accessions can be resolved for any input type.
    _ensure_assembly_summaries()

    n_gcx_input = len(gcx)
    unresolved_gcx = []

    if gcx:
        logger.info("Resolving %d assembly accessions to BioSample IDs...", len(gcx))
        resolved, unresolved_gcx = _resolve_assembly_to_biosample(gcx)
        samn = list(set(samn + resolved))

    if not samn:
        raise ValueError("No valid BioSample IDs could be resolved from the provided input.")

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

    # Final ingestion summary
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
    """
    Resolve GCF_/GCA_ accessions to BioSample accessions via the assembly flat files.

    Returns:
        resolved   -- list of BioSample accessions successfully resolved
        unresolved -- list of input GCF_/GCA_ accessions not found in either index
                      (suppressed, very new, or never submitted to assembly DB)
    """
    resolved = []
    gcx_set = set(gcx_ids)

    for label in ["refseq", "genbank"]:
        if not gcx_set:
            break
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            continue
        df = pd.read_csv(
            cache_path, sep="\t", skiprows=1,
            usecols=["# assembly_accession", "biosample"],
            low_memory=False,
        ).rename(columns={"# assembly_accession": "accession"})

        hits = df[df["accession"].isin(gcx_set) & df["biosample"].notna()]
        for acc, biosample in zip(hits["accession"], hits["biosample"]):
            if acc in gcx_set:
                resolved.append(biosample)
                gcx_set.discard(acc)

    unresolved = list(gcx_set)
    if unresolved:
        logger.warning(
            "%d assembly accessions not found in either index (suppressed or absent): %s",
            len(unresolved), unresolved[:5],
        )
    return resolved, unresolved


def _resolve_biosample_to_assembly(biosample_ids: set) -> dict:
    """
    For each BioSample accession, collect bioproject, GCF_ (refseq), and GCA_ (genbank)
    accessions from the NCBI assembly flat files.

    Returns a dict keyed by biosample accession:
      {
        "SAMN...": {
          "bioproject": "PRJNA...",
          "refseq":     "GCF_...",   # None if not in RefSeq
          "genbank":    "GCA_...",   # None if not in GenBank
        },
        ...
      }

    Both flat files are always checked independently so that a BioSample present
    in only one source still gets all available accessions populated.
    """
    lookup: dict = {}

    for label in ["refseq", "genbank"]:
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            continue
        try:
            df = pd.read_csv(
                cache_path, sep="\t", skiprows=1,
                usecols=["# assembly_accession", "biosample", "bioproject"],
                low_memory=False,
            ).rename(columns={"# assembly_accession": "assembly_accession"})

            hits = df[
                df["biosample"].isin(biosample_ids)
                & df["assembly_accession"].notna()
            ]

            for _, row in hits.iterrows():
                bs = row["biosample"]
                asm = row["assembly_accession"]
                bp = row["bioproject"] if pd.notna(row["bioproject"]) else None

                if bs not in lookup:
                    lookup[bs] = {"bioproject": None, "refseq": None, "genbank": None}

                if lookup[bs]["bioproject"] is None and bp:
                    lookup[bs]["bioproject"] = bp

                if asm.startswith("GCF_"):
                    if lookup[bs]["refseq"] is None:
                        lookup[bs]["refseq"] = asm
                elif asm.startswith("GCA_"):
                    if lookup[bs]["genbank"] is None:
                        lookup[bs]["genbank"] = asm

        except Exception as exc:
            logger.warning(
                "Could not read assembly index (%s) for resolution: %s", label, exc
            )

    return lookup


def _resolve_accessions_to_uids(accessions: list) -> dict:
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
            except Exception as exc:
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
                except Exception as exc:
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

    return acc_to_uid


def _fetch_biosample_metadata(samn_ids: list, synonym_lookup: dict = None) -> pd.DataFrame:
    inter_batch_sleep = 0.12 if ENTREZ_API_KEY else 0.34
    requested_set = set(samn_ids)

    logger.info("Resolving %d accessions to NCBI UIDs...", len(samn_ids))
    acc_to_uid = _resolve_accessions_to_uids(samn_ids)

    unresolved = [a for a in samn_ids if a not in acc_to_uid]
    if unresolved:
        logger.warning(
            "%d accessions could not be resolved to UIDs and will be skipped: %s",
            len(unresolved), unresolved[:5],
        )

    uid_list = list(acc_to_uid.values())
    if not uid_list:
        raise ValueError("No UIDs could be resolved from the provided BioSample accessions.")

    logger.info("Fetching metadata for %d resolved UIDs...", len(uid_list))

    records = []
    failed_ids = []
    total = len(uid_list)
    n_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE

    for batch_i, start in enumerate(range(0, total, _BATCH_SIZE)):
        uid_batch = uid_list[start:start + _BATCH_SIZE]
        batch_records = _fetch_batch_with_retry(uid_batch, synonym_lookup=synonym_lookup)

        if batch_records is None:
            logger.error(
                "Batch %d/%d failed after %d retries. %d records excluded.",
                batch_i + 1, n_batches, _MAX_RETRIES, len(uid_batch),
            )
            failed_ids.extend(uid_batch)
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

        fetched = min(start + _BATCH_SIZE, total)
        logger.info("Fetched %d / %d (%d/%d batches)", fetched, total, batch_i + 1, n_batches)
        if batch_i < n_batches - 1:
            time.sleep(inter_batch_sleep)

    if failed_ids:
        logger.warning("%d UIDs could not be fetched: %s", len(failed_ids), failed_ids[:10])

    return pd.DataFrame(records).reindex(columns=BIOSAMPLE_SCHEMA)


def _fetch_batch_with_retry(uid_batch: list, synonym_lookup: dict = None):
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
        except Exception as exc:
            wait = min(_RETRY_BASE_S ** attempt, _RETRY_MAX_S)
            logger.warning(
                "Batch fetch attempt %d/%d failed: %s. Retrying in %ds...",
                attempt, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    return None


def _parse_biosample_xml(xml_bytes: bytes, synonym_lookup: dict = None) -> list:
    """
    Parse raw BioSample XML bytes into a list of flat dicts with fixed schema.

    Resolution priority for each <Attribute>:
      1. If harmonized_name is present and is a known final output column,
         trust it directly (this is the official NCBI harmonization signal).
      2. Else if harmonized_name resolves via synonym_lookup, use that.
      3. Else if attribute_name resolves via synonym_lookup, use that.
      4. Else preserve the raw key/value inside _extra_attributes.
    """
    records = []
    root = ET.fromstring(xml_bytes)

    for sample in root.findall(".//BioSample"):
        record = dict.fromkeys(BIOSAMPLE_SCHEMA, None)

        record["biosample_accession"] = sample.get("accession")
        record["biosample_id"] = sample.get("id")
        record["submission_date"] = sample.get("submission_date")
        record["last_update"] = sample.get("last_update")
        record["publication_date"] = sample.get("publication_date")
        record["access"] = sample.get("access")

        for db_id in sample.findall(".//Id"):
            db = db_id.get("db", "")
            label = db_id.get("db_label", "")
            val = (db_id.text or "").strip()
            if db == "SRA":
                record["sra_accession"] = val
            elif db == "BioProject" and val:
                record["bioproject_accession"] = val
            elif label == "Sample name" and val:
                record["sample_name_id"] = val

        title_el = sample.find(".//Description/Title")
        record["title"] = title_el.text.strip() if title_el is not None and title_el.text else None

        comment_el = sample.find(".//Description/Comment/Paragraph")
        record["description_comment"] = (
            comment_el.text.strip() if comment_el is not None and comment_el.text else None
        )

        organism = sample.find(".//Organism")
        if organism is not None:
            record["taxonomy_id"] = organism.get("taxonomy_id")
            record["taxonomy_name"] = organism.get("taxonomy_name")
            org_name_el = organism.find(".//OrganismName")
            if org_name_el is not None and org_name_el.text:
                record["organism_name"] = org_name_el.text.strip()
            else:
                record["organism_name"] = organism.get("taxonomy_name")

        package_el = sample.find(".//Package")
        record["ncbi_package"] = package_el.text.strip() if package_el is not None and package_el.text else None

        status_el = sample.find(".//Status")
        if status_el is not None:
            record["status"] = status_el.get("status")
            record["status_date"] = status_el.get("when")

        extras = {}
        for attr in sample.findall(".//Attribute"):
            hn = (attr.get("harmonized_name") or "").strip()
            an = (attr.get("attribute_name") or "").strip()
            raw_key = hn or an or "unknown"
            val = (attr.text or "").strip() or None

            if val is None:
                continue

            resolved = None
            if hn and hn in BIOSAMPLE_SCHEMA_SET:
                resolved = hn
            elif synonym_lookup is not None and hn and hn.lower() in synonym_lookup:
                candidate = synonym_lookup[hn.lower()]
                if candidate in BIOSAMPLE_SCHEMA_SET:
                    resolved = candidate
            elif synonym_lookup is not None and an and an.lower() in synonym_lookup:
                candidate = synonym_lookup[an.lower()]
                if candidate in BIOSAMPLE_SCHEMA_SET:
                    resolved = candidate

            if resolved is not None:
                if record.get(resolved) is None:
                    record[resolved] = val
            else:
                extras[raw_key] = val

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
