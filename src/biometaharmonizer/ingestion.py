import pandas as pd
import requests
from pathlib import Path
from Bio import Entrez


ENTREZ_EMAIL = "your@email.com"  # Override via set_email()
ASSEMBLY_SUMMARY_REFSEQ = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt"
ASSEMBLY_SUMMARY_GENBANK = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt"


def set_email(email):
    """Set the Entrez email address required by NCBI for API calls."""
    global ENTREZ_EMAIL
    ENTREZ_EMAIL = email
    Entrez.email = email


def ingest(source):
    """
    Module 1: Universal Data Ingestion.

    Accepts either:
      - A path to a plain .txt file containing one accession per line.
        Accessions can be BioSample IDs (SAMN/SAME/SAMD) or
        genome assembly IDs (GCF_/GCA_). Mixed files are handled.
      - A Python list of accession strings.

    Returns a normalized pandas DataFrame of raw BioSample metadata.
    """
    ids = _load_ids(source)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        print(f"[WARNING] {len(unrecognized)} unrecognized IDs skipped: {unrecognized[:5]}")

    if gcx:
        print(f"[INFO] Resolving {len(gcx)} assembly accessions to BioSample IDs...")
        resolved = _resolve_assembly_to_biosample(gcx)
        samn = list(set(samn + resolved))

    if not samn:
        raise ValueError("No valid BioSample IDs could be resolved from the provided input.")

    print(f"[INFO] Fetching metadata for {len(samn)} BioSample accessions...")
    return _fetch_biosample_metadata(samn)


def _load_ids(source):
    """Load IDs from a file path, Path object, or a Python list."""
    if isinstance(source, list):
        return [s.strip() for s in source if s.strip()]
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with open(path, "r") as f:
        return [line.strip() for line in f if line.strip()]


def _classify_ids(ids):
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


def _resolve_assembly_to_biosample(gcx_ids):
    """
    Resolve GCF_/GCA_ accessions to BioSample IDs using NCBI
    assembly_summary flat files (RefSeq first, GenBank fallback).
    Downloads flat files on first call; subsequent calls use cached files.
    """
    resolved = []
    gcx_set = set(gcx_ids)

    for url, label in [
        (ASSEMBLY_SUMMARY_REFSEQ, "RefSeq"),
        (ASSEMBLY_SUMMARY_GENBANK, "GenBank")
    ]:
        if not gcx_set:
            break
        cache_path = Path(f"assembly_summary_{label.lower()}.txt")
        if not cache_path.exists():
            print(f"[INFO] Downloading {label} assembly summary (~100MB)...")
            _download_file(url, cache_path)

        df = pd.read_csv(
            cache_path, sep="\t", skiprows=1,
            usecols=["# assembly_accession", "biosample"],
            low_memory=False
        ).rename(columns={"# assembly_accession": "accession"})

        hits = df[df["accession"].isin(gcx_set)]
        for _, row in hits.iterrows():
            if pd.notna(row["biosample"]):
                resolved.append(row["biosample"])
                gcx_set.discard(row["accession"])

    if gcx_set:
        print(f"[WARNING] {len(gcx_set)} assembly accessions could not be resolved: {list(gcx_set)[:5]}")

    return resolved


def _fetch_biosample_metadata(samn_ids):
    """
    Fetch raw BioSample attribute metadata for a list of BioSample accessions
    using the NCBI Entrez efetch API in batches of 500.
    Returns a flattened pandas DataFrame.
    """
    Entrez.email = ENTREZ_EMAIL
    records = []
    batch_size = 500

    for i in range(0, len(samn_ids), batch_size):
        batch = samn_ids[i:i + batch_size]
        handle = Entrez.efetch(db="biosample", id=",".join(batch), rettype="full", retmode="xml")
        raw = handle.read()
        handle.close()
        records.extend(_parse_biosample_xml(raw))
        print(f"[INFO] Fetched {min(i + batch_size, len(samn_ids))} / {len(samn_ids)}")

    return pd.DataFrame(records)


def _parse_biosample_xml(xml_bytes):
    """Parse raw BioSample XML into a list of flat attribute dicts."""
    import xml.etree.ElementTree as ET
    records = []
    root = ET.fromstring(xml_bytes)
    for sample in root.findall(".//BioSample"):
        record = {
            "biosample_accession": sample.get("accession"),
            "biosample_id": sample.get("id"),
        }
        for attr in sample.findall(".//Attribute"):
            key = attr.get("harmonized_name") or attr.get("attribute_name", "unknown")
            record[key] = attr.text
        records.append(record)
    return records


def _download_file(url, dest_path):
    """Stream-download a large file to disk."""
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
