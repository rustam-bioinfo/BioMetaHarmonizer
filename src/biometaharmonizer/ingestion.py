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
"""

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from Bio import Entrez


logger = logging.getLogger(__name__)


# ─── Module-level configuration ──────────────────────────────────────────────

ENTREZ_EMAIL   = "your@email.com"   # Override via set_email()
ENTREZ_API_KEY = None               # Override via set_api_key()
CACHE_DIR      = Path.home() / ".biometaharmonizer" / "cache"

ASSEMBLY_SUMMARY_REFSEQ  = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt"
ASSEMBLY_SUMMARY_GENBANK = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt"

_BATCH_SIZE     = 200   # UIDs per efetch call; kept moderate for reliability
_ESEARCH_BATCH  = 200   # accessions per esearch OR query
_MAX_RETRIES    = 3
_RETRY_BASE_S   = 2     # seconds; exponential backoff: 2, 4, 8
_CACHE_TTL_DAYS = 7     # refresh cache if older than this many days


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


# ─── Public API ────────────────────────────────────────────────────────────────

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

    Note on bioproject_accession:
      BioProject is not stored in BioSample XML. It is resolved from the NCBI
      assembly summary flat files (RefSeq + GenBank) which contain a
      biosample -> bioproject mapping. Records with no assembly submission will
      have bioproject_accession = None after resolution.
    """
    if api_key is not None:
        set_api_key(api_key)
    if cache_dir is not None:
        set_cache_dir(cache_dir)

    Entrez.email   = ENTREZ_EMAIL
    Entrez.api_key = ENTREZ_API_KEY

    ids = _load_ids(source)
    ids = _deduplicate(ids)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        print(f"[WARNING] {len(unrecognized)} unrecognized IDs skipped: {unrecognized[:5]}")

    _ensure_assembly_summaries()

    if gcx:
        print(f"[INFO] Resolving {len(gcx)} assembly accessions to BioSample IDs...")
        resolved = _resolve_assembly_to_biosample(gcx)
        samn = list(set(samn + resolved))

    if not samn:
        raise ValueError("No valid BioSample IDs could be resolved from the provided input.")

    print(f"[INFO] Fetching metadata for {len(samn)} BioSample accessions...")
    df = _fetch_biosample_metadata(samn)

    print("[INFO] Resolving BioProject accessions from assembly index...")
    bioproject_map = _resolve_biosample_to_bioproject(set(df["biosample_accession"].dropna()))
    if bioproject_map:
        df["bioproject_accession"] = df["biosample_accession"].map(bioproject_map).fillna(
            df["bioproject_accession"]
        )
        filled = df["bioproject_accession"].notna().sum()
        print(f"[INFO] BioProject accession resolved for {filled} / {len(df)} records.")
    else:
        print("[WARNING] No BioProject accessions found in assembly index for this dataset.")

    return df


# ─── Internal helpers ───────────────────────────────────────────────────────────

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
        print(f"[INFO] Removed {n_dupes} duplicate input IDs.")
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
            print(f"[INFO] Fetching NCBI assembly index ({label}) -- this runs once and may take a moment...")
            _download_file(url, cache_path)
            print(f"[INFO] Assembly index ({label}) ready.")


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
        hits = df[df["accession"].isin(gcx_set)]
        for _, row in hits.iterrows():
            if pd.notna(row["biosample"]):
                resolved.append(row["biosample"])
                gcx_set.discard(row["accession"])

    if gcx_set:
        print(f"[WARNING] {len(gcx_set)} assembly accessions could not be resolved: {list(gcx_set)[:5]}")
    return resolved


def _resolve_biosample_to_bioproject(biosample_ids: set) -> dict:
    """
    Build a {biosample_accession: bioproject_accession} lookup from cached flat files.
    """
    lookup = {}
    for label in ["refseq", "genbank"]:
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
            for _, row in hits.iterrows():
                if row["biosample"] not in lookup:
                    lookup[row["biosample"]] = row["bioproject"]
        except Exception as exc:
            print(f"[WARNING] Could not read assembly index ({label}) for BioProject resolution: {exc}")
    return lookup


def _resolve_accessions_to_uids(accessions: list) -> dict:
    """
    Resolve a list of BioSample accessions (SAMN/SAME/SAMD) to NCBI
    integer UIDs via Entrez esearch, using batched OR queries.

    Returns a dict mapping accession (str) -> uid (str).
    Accessions that esearch cannot resolve are absent from the result
    and will be logged as warnings by the caller.

    Using esearch([Accession] field) is the only reliable way to obtain
    the canonical UID for a BioSample accession. Passing accession strings
    directly to efetch(db=biosample) is unreliable: NCBI interprets the
    id parameter as a numeric UID list, so non-numeric accession strings
    can match arbitrary unrelated records.
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
                wait = _RETRY_BASE_S ** attempt
                print(f"[WARNING] esearch attempt {attempt}/{_MAX_RETRIES} failed: {exc}. "
                      f"Retrying in {wait}s...")
                time.sleep(wait)
        else:
            print(f"[WARNING] esearch failed for batch starting at index {start}. "
                  f"These accessions will be skipped.")
            continue

        uids = result.get("IdList", [])
        if not uids:
            continue

        # Fetch summaries to map uid -> accession
        for uid_start in range(0, len(uids), _ESEARCH_BATCH):
            uid_batch = uids[uid_start:uid_start + _ESEARCH_BATCH]
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    sum_handle = Entrez.esummary(db="biosample", id=",".join(uid_batch))
                    summaries  = Entrez.read(sum_handle)
                    sum_handle.close()
                    break
                except Exception as exc:
                    wait = _RETRY_BASE_S ** attempt
                    print(f"[WARNING] esummary attempt {attempt}/{_MAX_RETRIES} failed: {exc}. "
                          f"Retrying in {wait}s...")
                    time.sleep(wait)
            else:
                continue

            for doc in summaries["DocumentSummarySet"]["DocumentSummary"]:
                uid = doc.attributes.get("uid", "")
                # The accession is stored in the SampleIds/Id block or
                # can be extracted from the Accession field of the XML.
                acc = doc.get("Accession", "")
                if acc and uid:
                    acc_to_uid[acc] = uid

        time.sleep(inter_req_sleep)

    return acc_to_uid


def _fetch_biosample_metadata(samn_ids: list) -> pd.DataFrame:
    """
    Fetch raw BioSample attribute metadata via a two-step process:

    Step 1 -- esearch: resolve every BioSample accession to its canonical
    NCBI integer UID. This is necessary because efetch(db=biosample)
    treats the id parameter as a numeric UID list; passing accession
    strings directly causes NCBI to interpret them as numeric IDs and
    can return completely unrelated records.

    Step 2 -- efetch by UID: fetch full XML for resolved UIDs in batches.
    After parsing, every record is validated: if its biosample_accession
    attribute is not in the requested input set, the record is dropped and
    logged as a warning. This makes silent cross-contamination impossible.
    """
    inter_batch_sleep = 0.12 if ENTREZ_API_KEY else 0.34
    requested_set = set(samn_ids)

    # ─── Step 1: resolve accessions to UIDs ────────────────────────────────
    print(f"[INFO] Resolving {len(samn_ids)} accessions to NCBI UIDs...")
    acc_to_uid = _resolve_accessions_to_uids(samn_ids)

    unresolved = [a for a in samn_ids if a not in acc_to_uid]
    if unresolved:
        print(f"[WARNING] {len(unresolved)} accessions could not be resolved to UIDs "
              f"and will be skipped: {unresolved[:5]}")

    uid_list = list(acc_to_uid.values())
    if not uid_list:
        raise ValueError("No UIDs could be resolved from the provided BioSample accessions.")

    print(f"[INFO] Fetching metadata for {len(uid_list)} resolved UIDs...")

    # ─── Step 2: efetch by UID in batches ────────────────────────────────
    records    = []
    failed_ids = []
    total      = len(uid_list)
    n_batches  = (total + _BATCH_SIZE - 1) // _BATCH_SIZE

    for batch_i, start in enumerate(range(0, total, _BATCH_SIZE)):
        uid_batch = uid_list[start:start + _BATCH_SIZE]
        batch_records = _fetch_batch_with_retry(uid_batch)

        if batch_records is None:
            print(
                f"[ERROR] Batch {batch_i + 1}/{n_batches} failed after {_MAX_RETRIES} retries. "
                f"{len(uid_batch)} records excluded."
            )
            failed_ids.extend(uid_batch)
        else:
            # Validate: drop any record whose accession is not in the requested set
            clean = []
            for rec in batch_records:
                acc = rec.get("biosample_accession", "")
                if acc in requested_set:
                    clean.append(rec)
                else:
                    print(f"[WARNING] Unexpected record returned by NCBI and discarded: "
                          f"biosample_accession={acc!r} (not in requested input set)")
            records.extend(clean)

        fetched = min(start + _BATCH_SIZE, total)
        print(f"[INFO] Fetched {fetched} / {total} ({batch_i + 1}/{n_batches} batches)")
        if batch_i < n_batches - 1:
            time.sleep(inter_batch_sleep)

    if failed_ids:
        print(f"[WARNING] {len(failed_ids)} UIDs could not be fetched: {failed_ids[:10]}")

    return pd.DataFrame(records)


def _fetch_batch_with_retry(uid_batch: list):
    """
    Fetch a single batch of integer UIDs via Entrez efetch.
    Returns a list of record dicts on success, or None after all retries fail.
    """
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
            return _parse_biosample_xml(raw)
        except Exception as exc:
            wait = _RETRY_BASE_S ** attempt
            print(f"[WARNING] Batch fetch attempt {attempt}/{_MAX_RETRIES} failed: {exc}. "
                  f"Retrying in {wait}s...")
            time.sleep(wait)
    return None


def _parse_biosample_xml(xml_bytes: bytes) -> list:
    """
    Parse raw BioSample XML bytes into a list of flat attribute dicts.
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

        for attr in sample.findall(".//Attribute"):
            key = attr.get("harmonized_name") or attr.get("attribute_name", "unknown")
            val = (attr.text or "").strip()
            record[key] = val if val else None

        records.append(record)

    return records


def _download_file(url: str, dest_path: Path) -> None:
    """Stream-download a large file to disk."""
    with requests.get(url, stream=True) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)
