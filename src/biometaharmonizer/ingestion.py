"""
Module 1: Universal Data Ingestion.

Fetches NCBI BioSample metadata for lists of BioSample IDs or assembly accessions.
BioProject accession is resolved from NCBI assembly summary flat files, which are
downloaded once to a configurable cache directory and reused on subsequent runs.

Entrez rate limits:
  Without API key: 3 requests/second
  With API key:   10 requests/second
Register a free API key at https://www.ncbi.nlm.nih.gov/account/ and pass it to
set_api_key() or ingest(api_key=...).

Working directory note (Colab):
  Assembly summary flat files (~100 MB each) are cached in CACHE_DIR, which defaults
  to ~/.biometaharmonizer/cache/. In Colab, override with set_cache_dir("/content")
  if you want them in the working directory.
"""

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from Bio import Entrez


# ─── Module-level configuration ──────────────────────────────────────────────

ENTREZ_EMAIL   = "your@email.com"   # Override via set_email()
ENTREZ_API_KEY = None               # Override via set_api_key()
CACHE_DIR      = Path.home() / ".biometaharmonizer" / "cache"

ASSEMBLY_SUMMARY_REFSEQ  = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt"
ASSEMBLY_SUMMARY_GENBANK = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt"

_BATCH_SIZE   = 500
_MAX_RETRIES  = 3
_RETRY_BASE_S = 2   # seconds; exponential backoff: 2, 4, 8


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


# ─── Public API ──────────────────────────────────────────────────────────────

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

    print("[INFO] Resolving BioProject accessions from assembly summary flat files...")
    bioproject_map = _resolve_biosample_to_bioproject(set(df["biosample_accession"].dropna()))
    if bioproject_map:
        df["bioproject_accession"] = df["biosample_accession"].map(bioproject_map)
        filled = df["bioproject_accession"].notna().sum()
        print(f"[INFO] BioProject accession resolved for {filled} / {len(df)} records.")
    else:
        print("[WARNING] No BioProject accessions found in assembly summary files for this dataset.")

    return df


# ─── Internal helpers ─────────────────────────────────────────────────────────

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
        if i.startswith(("GCF_", "GCA_")):
            gcx.append(i)
        elif i.upper().startswith(("SAMN", "SAME", "SAMD")):
            samn.append(i)
        else:
            unrecognized.append(i)
    return gcx, samn, unrecognized


def _ensure_assembly_summaries() -> None:
    """
    Download and cache NCBI assembly summary flat files if not already present.
    Files are stored in CACHE_DIR (~/.biometaharmonizer/cache/ by default).
    Called unconditionally by ingest() so both resolution functions always have
    the files regardless of input ID type.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url, label in [
        (ASSEMBLY_SUMMARY_REFSEQ,  "refseq"),
        (ASSEMBLY_SUMMARY_GENBANK, "genbank"),
    ]:
        cache_path = CACHE_DIR / f"assembly_summary_{label}.txt"
        if not cache_path.exists():
            print(f"[INFO] Downloading {label} assembly summary (~100 MB) to {cache_path}...")
            _download_file(url, cache_path)
            print(f"[INFO] Saved: {cache_path}")


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
    BioProject is not in BioSample XML; the assembly summary files are the canonical source.
    Returns an empty dict if neither flat file is readable.
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
            print(f"[WARNING] Could not read {cache_path} for BioProject resolution: {exc}")
    return lookup


def _fetch_biosample_metadata(samn_ids: list) -> pd.DataFrame:
    """
    Fetch raw BioSample attribute metadata via NCBI Entrez efetch in batches.
    Retries each failed batch up to _MAX_RETRIES times with exponential backoff.
    Accessions that fail after all retries are logged and excluded from output.
    """
    records      = []
    failed_ids   = []
    total        = len(samn_ids)
    n_batches    = (total + _BATCH_SIZE - 1) // _BATCH_SIZE
    # Respect NCBI rate limit: 3 req/s without API key, 10 with
    inter_batch_sleep = 0.12 if ENTREZ_API_KEY else 0.34

    for batch_i, start in enumerate(range(0, total, _BATCH_SIZE)):
        batch = samn_ids[start:start + _BATCH_SIZE]
        batch_records = _fetch_batch_with_retry(batch)
        if batch_records is None:
            print(
                f"[ERROR] Batch {batch_i + 1}/{n_batches} failed after {_MAX_RETRIES} retries. "
                f"{len(batch)} records excluded."
            )
            failed_ids.extend(batch)
        else:
            records.extend(batch_records)
        fetched = min(start + _BATCH_SIZE, total)
        print(f"[INFO] Fetched {fetched} / {total} ({batch_i + 1}/{n_batches} batches)")
        if batch_i < n_batches - 1:
            time.sleep(inter_batch_sleep)

    if failed_ids:
        print(f"[WARNING] {len(failed_ids)} accessions could not be fetched: {failed_ids[:10]}")

    return pd.DataFrame(records)


def _fetch_batch_with_retry(batch: list):
    """
    Fetch a single batch via Entrez efetch with exponential-backoff retry.
    Returns a list of record dicts on success, or None after all retries fail.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            handle = Entrez.efetch(
                db="biosample",
                id=",".join(batch),
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

    Structural fields extracted:
      - BioSample element attributes: accession, id, submission_date, last_update,
        publication_date, access
      - <Ids> block: sra_accession (db=SRA), sample_name_id (db_label=Sample name),
        bioproject_accession from XML as rare fallback (primary resolution via flat files)
      - <Description>: title, description_comment, taxonomy_id, taxonomy_name
        organism_name = taxonomy_name (no <OrganismName> child exists in NCBI BioSample XML)
      - <Package>: ncbi_package
      - <Status>: status, status_date
      - All <Attribute> key-value pairs (empty values stored as None)
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
