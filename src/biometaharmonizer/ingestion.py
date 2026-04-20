"""
Module 1: Universal Data Ingestion.

Fetches NCBI BioSample metadata for lists of BioSample IDs or assembly accessions.
BioProject accession is resolved from NCBI assembly summary flat files, which are
downloaded once to a configurable cache directory and refreshed every 7 days.

Entrez rate limits:
  Without API key: 3 requests/second (1 batch = 1 request)
  With API key:   10 requests/second
Register a free API key at https://www.ncbi.nlm.nih.gov/account/ and pass it to
set_api_key() or ingest(api_key=...).

Working directory note (Colab):
  Assembly summary flat files (~100 MB each) are cached in CACHE_DIR, which defaults
  to ~/.biometaharmonizer/cache/. In Colab, override with set_cache_dir("/content")
  if you want them in the working directory.

Fix notes (vs. original):
  #1  raw_key / hn aliasing: attribute_name lookup now uses a dedicated
      `an` variable, distinct from `hn`, so the third synonym-lookup branch
      is always reachable when harmonized_name is absent from the synonym dict.
  #2  CACHE_DIR typo: all references to the cache directory now use the
      module-level CACHE_DIR name consistently (was CACHE_D in one call site).
  #3  esearch batch size reduced to 100 to stay well within NCBI URL length
      limits; usehistory='n' kept for simplicity at this batch size.
  #4  None-safe accession check: acc is normalised to '' before set membership
      test so that suppressed records with no accession are handled correctly.
  #5  Conditional assembly summary download: _ensure_assembly_summaries() is
      called only when there are GCF_/GCA_ accessions to resolve, avoiding
      unnecessary ~200 MB downloads for BioSample-only input.
"""

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

_DEFAULT_EMAIL = "your@email.com"   # sentinel; must be overridden via set_email()
ENTREZ_EMAIL   = _DEFAULT_EMAIL
ENTREZ_API_KEY = None               # Override via set_api_key()
CACHE_DIR      = Path.home() / ".biometaharmonizer" / "cache"

ASSEMBLY_SUMMARY_REFSEQ  = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt"
ASSEMBLY_SUMMARY_GENBANK = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt"

_BATCH_SIZE     = 200   # UIDs per efetch call
# Fix #3: reduced from 200 to 100 to stay within NCBI URL length limits for
# OR-query esearch.  At 100 x ~14 chars + " OR " overhead the query is ~1,800
# characters, well below the 8,000-byte NCBI ceiling.
_ESEARCH_BATCH  = 100
_MAX_RETRIES    = 3
_RETRY_BASE_S   = 2     # exponential backoff base: min(base**attempt, 30)
_RETRY_MAX_S    = 30
_CACHE_TTL_DAYS = 7


def set_email(email: str) -> None:
    """Set the Entrez email address required by NCBI for all API calls."""
    global ENTREZ_EMAIL
    ENTREZ_EMAIL = email
    Entrez.email = email


def set_api_key(key: str) -> None:
    """
    Set the NCBI Entrez API key.
    With a key, the rate limit rises from 3 to 10 requests/second.
    Register at https://www.ncbi.nlm.nih.gov/account/
    """
    global ENTREZ_API_KEY
    ENTREZ_API_KEY = key
    Entrez.api_key = key


def set_cache_dir(path) -> None:
    """Override the directory used for assembly summary flat-file caches."""
    global CACHE_DIR
    CACHE_DIR = Path(path)


# --- Public API ------------------------------------------------------------

def ingest(source, api_key: str = None, cache_dir=None) -> pd.DataFrame:
    """
    Module 1: Universal Data Ingestion.

    Accepts either:
      - A path to a plain .txt file with one accession per line.
        Accessions may be BioSample IDs (SAMN/SAME/SAMD) or
        assembly accessions (GCF_/GCA_). Mixed files are handled.
      - A Python list of accession strings.

    Parameters
    ----------
    source : str, Path, or list
        Input accessions.
    api_key : str, optional
        NCBI API key for 10 req/s rate limit. Overrides set_api_key().
    cache_dir : str or Path, optional
        Directory for assembly summary flat-file caches. Overrides set_cache_dir().

    Returns
    -------
    pd.DataFrame
        Raw BioSample metadata. Structural fields are always present.
        Records that could not be fetched after all retries are logged and skipped.

    Raises
    ------
    ValueError
        If set_email() has not been called with a real address before ingesting.
    """
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

    Entrez.email   = ENTREZ_EMAIL
    Entrez.api_key = ENTREZ_API_KEY

    ids = _load_ids(source)
    ids = _deduplicate(ids)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        logger.warning("%d unrecognized IDs skipped: %s", len(unrecognized), unrecognized[:5])

    # Fix #5: only download assembly summaries when GCF_/GCA_ accessions are
    # present; skip for BioSample-only input to avoid unnecessary ~200 MB download.
    if gcx:
        _ensure_assembly_summaries()
        logger.info("Resolving %d assembly accessions to BioSample IDs...", len(gcx))
        resolved = _resolve_assembly_to_biosample(gcx)
        samn = list(set(samn + resolved))

    if not samn:
        raise ValueError("No valid BioSample IDs could be resolved from the provided input.")

    synonym_lookup = build_synonym_lookup()
    logger.info("Fetching metadata for %d BioSample accessions...", len(samn))
    df = _fetch_biosample_metadata(samn, synonym_lookup=synonym_lookup)

    # Fix #2: use CACHE_DIR consistently (was CACHE_D in the BioProject call).
    logger.info("Resolving BioProject accessions from assembly index...")
    bioproject_map = _resolve_biosample_to_bioproject(set(df["biosample_accession"].dropna()))
    if bioproject_map:
        df["bioproject_accession"] = df["biosample_accession"].map(bioproject_map).fillna(
            df["bioproject_accession"]
        )
        filled = df["bioproject_accession"].notna().sum()
        logger.info("BioProject accession resolved for %d / %d records.", filled, len(df))
    else:
        logger.warning("No BioProject accessions found in assembly index for this dataset.")

    return df


# --- Internal helpers ------------------------------------------------------

def _load_ids(source) -> list:
    """Load IDs from a file path, Path object, or a Python list."""
    if isinstance(source, list):
        return [s.strip() for s in source if s.strip()]
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with open(path, "r") as fh:
        return [line.strip() for line in fh if line.strip()]


def _deduplicate(ids: list) -> list:
    """Remove duplicate IDs while preserving order."""
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
    """Separate a mixed list of IDs into assembly, BioSample, and unrecognized."""
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
    """
    Ensure NCBI assembly summary flat files are present in CACHE_DIR.
    Files are downloaded on first use and refreshed when older than
    _CACHE_TTL_DAYS days.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url, label in [
        (ASSEMBLY_SUMMARY_REFSEQ,  "refseq"),
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


def _resolve_assembly_to_biosample(gcx_ids: list) -> list:
    """
    Resolve GCF_/GCA_ accessions to BioSample IDs using cached flat files.
    RefSeq is checked first; unresolved IDs fall through to GenBank.
    """
    resolved = []
    gcx_set  = set(gcx_ids)

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

    if gcx_set:
        logger.warning(
            "%d assembly accessions could not be resolved: %s",
            len(gcx_set), list(gcx_set)[:5],
        )
    return resolved


def _resolve_biosample_to_bioproject(biosample_ids: set) -> dict:
    """
    Build a {biosample_accession: bioproject_accession} lookup from cached
    flat files. Uses CACHE_DIR (fix #2: was CACHE_D in original).
    """
    lookup = {}
    for label in ["refseq", "genbank"]:
        # Fix #2: reference CACHE_DIR, not the undefined CACHE_D.
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            continue
        try:
            df = pd.read_csv(
                cache_path, sep="\t", skiprows=1,
                usecols=["biosample", "bioproject"],
                low_memory=False,
            )
            hits = df[df["biosample"].isin(biosample_ids) & df["bioproject"].notna()]
            for biosample, bioproject in zip(hits["biosample"], hits["bioproject"]):
                if biosample not in lookup:
                    lookup[biosample] = bioproject
        except Exception as exc:
            logger.warning(
                "Could not read assembly index (%s) for BioProject resolution: %s",
                label, exc,
            )
    return lookup


def _resolve_accessions_to_uids(accessions: list) -> dict:
    """
    Resolve BioSample accessions to NCBI integer UIDs via batched OR-query
    esearch.  Batch size is _ESEARCH_BATCH (100) to stay within NCBI URL
    length limits (fix #3).
    """
    inter_req_sleep = 0.12 if ENTREZ_API_KEY else 0.34
    acc_to_uid = {}

    for start in range(0, len(accessions), _ESEARCH_BATCH):
        batch = accessions[start:start + _ESEARCH_BATCH]
        term  = " OR ".join(f"{acc}[Accession]" for acc in batch)

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
                "esearch failed for batch starting at index %d. "
                "These accessions will be skipped.",
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
                    summaries  = Entrez.read(sum_handle)
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
    """
    Fetch raw BioSample attribute metadata via a two-step process:

    Step 1 -- esearch: resolve every BioSample accession to its canonical
    NCBI integer UID.

    Step 2 -- efetch by UID: fetch full XML for resolved UIDs in batches.
    After parsing, every record is validated against the requested input set.
    """
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

    records    = []
    failed_ids = []
    total      = len(uid_list)
    n_batches  = (total + _BATCH_SIZE - 1) // _BATCH_SIZE

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
                # Fix #4: normalise None accession to '' before set membership test.
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

    return pd.DataFrame(records)


def _fetch_batch_with_retry(uid_batch: list, synonym_lookup: dict = None):
    """Fetch a single UID batch via efetch; return list of dicts or None."""
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


# --- Attribute resolution at parse time ------------------------------------

def _parse_biosample_xml(xml_bytes: bytes, synonym_lookup: dict = None) -> list:
    """
    Parse raw BioSample XML bytes into a list of flat attribute dicts.

    Fix #1: the original code set raw_key = harmonized_name or attribute_name
    and then used raw_key for both the second and third synonym-lookup branches,
    making the attribute_name branch unreachable when harmonized_name was set
    but absent from the lookup.  This version uses three independent variables:
      hn  -- harmonized_name from the XML element
      an  -- attribute_name from the XML element
      raw_key -- whichever of hn/an is non-empty (used only as the extras key)
    so all three branches are always reachable.
    """
    records = []
    root    = ET.fromstring(xml_bytes)

    for sample in root.findall(".//BioSample"):
        record = {}

        record["biosample_accession"] = sample.get("accession")
        record["biosample_id"]        = sample.get("id")
        record["submission_date"]     = sample.get("submission_date")
        record["last_update"]         = sample.get("last_update")
        record["publication_date"]    = sample.get("publication_date")
        record["access"]              = sample.get("access")

        record["bioproject_accession"] = None
        for db_id in sample.findall(".//Id"):
            db    = db_id.get("db", "")
            label = db_id.get("db_label", "")
            val   = (db_id.text or "").strip()
            if db == "SRA":
                record["sra_accession"] = val
            elif db == "BioProject" and val:
                record["bioproject_accession"] = val
            elif label == "Sample name" and val:
                record["sample_name_id"] = val

        title_el = sample.find(".//Description/Title")
        record["title"] = (
            title_el.text.strip()
            if title_el is not None and title_el.text
            else None
        )

        comment_el = sample.find(".//Description/Comment/Paragraph")
        record["description_comment"] = (
            comment_el.text.strip()
            if comment_el is not None and comment_el.text
            else None
        )

        organism = sample.find(".//Organism")
        if organism is not None:
            record["taxonomy_id"]   = organism.get("taxonomy_id")
            record["taxonomy_name"] = organism.get("taxonomy_name")
            org_name_el = organism.find(".//OrganismName")
            if org_name_el is not None and org_name_el.text:
                record["organism_name"] = org_name_el.text.strip()
            else:
                record["organism_name"] = organism.get("taxonomy_name")
        else:
            record["taxonomy_id"]   = None
            record["taxonomy_name"] = None
            record["organism_name"] = None

        package_el = sample.find(".//Package")
        record["ncbi_package"] = (
            package_el.text.strip()
            if package_el is not None and package_el.text
            else None
        )

        status_el = sample.find(".//Status")
        if status_el is not None:
            record["status"]      = status_el.get("status")
            record["status_date"] = status_el.get("when")
        else:
            record["status"]      = None
            record["status_date"] = None

        # --- Attribute resolution (fix #1) ---
        extras = {}
        for attr in sample.findall(".//Attribute"):
            hn  = (attr.get("harmonized_name") or "").strip()
            an  = (attr.get("attribute_name")  or "").strip()
            # raw_key is used only as the fallback key for the extras dict.
            raw_key = hn or an or "unknown"
            val = (attr.text or "").strip() or None

            if synonym_lookup is not None:
                resolved = None
                # Branch 1: harmonized_name present and in lookup.
                if hn and hn.lower() in synonym_lookup:
                    resolved = synonym_lookup[hn.lower()]
                # Branch 2: raw_key (== hn when hn is set, else an) in lookup.
                elif raw_key.lower() in synonym_lookup:
                    resolved = synonym_lookup[raw_key.lower()]
                # Branch 3 (fix #1): attribute_name independently in lookup.
                # This branch is now reachable even when hn is set but absent
                # from the lookup, because `an` is a separate variable.
                elif an and an.lower() in synonym_lookup:
                    resolved = synonym_lookup[an.lower()]

                if resolved is not None:
                    if resolved not in record or record[resolved] is None:
                        record[resolved] = val
                else:
                    if val is not None:
                        extras[raw_key] = val
            else:
                record[raw_key] = val

        if synonym_lookup is not None:
            record["_extra_attributes"] = json.dumps(extras) if extras else None

        records.append(record)

    return records


def _download_file(url: str, dest_path: Path) -> None:
    """
    Stream-download a large file to disk with atomic rename on success.
    Partial temporary files are removed on failure.
    """
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
