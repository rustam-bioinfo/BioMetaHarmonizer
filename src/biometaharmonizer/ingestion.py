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
    Structural fields (taxonomy, SRA, BioProject, dates, status) are
    always present as fixed columns regardless of schema used.

    Note on bioproject_accession:
      BioProject is not stored in BioSample XML. It is resolved from
      the NCBI assembly summary flat files (RefSeq + GenBank) which
      contain a biosample -> bioproject mapping. Files are downloaded
      once (~100 MB each) and cached on disk for subsequent runs.
      Records with no assembly submission will have bioproject_accession
      = None after resolution.
    """
    ids = _load_ids(source)
    gcx, samn, unrecognized = _classify_ids(ids)

    if unrecognized:
        print(f"[WARNING] {len(unrecognized)} unrecognized IDs skipped: {unrecognized[:5]}")

    # ensure flat files are on disk before any resolution step
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


def _ensure_assembly_summaries():
    """
    Download and cache NCBI assembly summary flat files if not already
    present on disk. Called unconditionally by ingest() so that both
    _resolve_assembly_to_biosample() and _resolve_biosample_to_bioproject()
    always have the files available regardless of input ID type.
    """
    for url, label in [
        (ASSEMBLY_SUMMARY_REFSEQ, "refseq"),
        (ASSEMBLY_SUMMARY_GENBANK, "genbank"),
    ]:
        cache_path = Path(f"assembly_summary_{label}.txt")
        if not cache_path.exists():
            print(f"[INFO] Downloading {label} assembly summary (~100 MB)...")
            _download_file(url, cache_path)
            print(f"[INFO] Saved to {cache_path}")


def _resolve_assembly_to_biosample(gcx_ids):
    """
    Resolve GCF_/GCA_ accessions to BioSample IDs using cached
    NCBI assembly summary flat files (RefSeq first, GenBank fallback).
    Assumes _ensure_assembly_summaries() has already been called.
    """
    resolved = []
    gcx_set = set(gcx_ids)

    for label in ["refseq", "genbank"]:
        if not gcx_set:
            break
        cache_path = Path(f"assembly_summary_{label}.txt")
        if not cache_path.exists():
            continue

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


def _resolve_biosample_to_bioproject(biosample_ids):
    """
    Build a {biosample_accession: bioproject_accession} lookup dict
    from cached NCBI assembly summary flat files.

    BioProject accession is not stored in BioSample XML. This function
    uses the assembly summary files which contain both biosample and
    bioproject columns for every genome assembly deposited in NCBI.
    Assumes _ensure_assembly_summaries() has already been called.

    Returns an empty dict if neither flat file is readable.
    """
    lookup = {}

    for label in ["refseq", "genbank"]:
        cache_path = Path(f"assembly_summary_{label}.txt")
        if not cache_path.exists():
            continue
        try:
            df = pd.read_csv(
                cache_path, sep="\t", skiprows=1,
                usecols=["biosample", "bioproject"],
                low_memory=False
            )
            hits = df[df["biosample"].isin(biosample_ids) & df["bioproject"].notna()]
            for _, row in hits.iterrows():
                if row["biosample"] not in lookup:
                    lookup[row["biosample"]] = row["bioproject"]
        except Exception as e:
            print(f"[WARNING] Could not read {cache_path} for BioProject resolution: {e}")

    return lookup


def _fetch_biosample_metadata(samn_ids):
    """
    Fetch raw BioSample attribute metadata for a list of BioSample accessions
    using the NCBI Entrez efetch API in batches of 500.
    Returns a flattened pandas DataFrame with bioproject_accession = None
    (patched by ingest() after flat file resolution).
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
    """
    Parse raw BioSample XML into a list of flat attribute dicts.

    Structural fields extracted:
      - BioSample element: accession, id, submission_date, last_update,
        publication_date, access
      - <Ids> block: sra_accession (db=SRA), sample_name_id (db_label=Sample name)
        bioproject_accession from <Ids> is kept as a rare XML fallback only;
        primary resolution is via assembly summary flat files in ingest().
      - <Description>: title, description_comment (Comment/Paragraph),
        taxonomy_id and taxonomy_name from <Organism> element attributes.
        organism_name = taxonomy_name (no OrganismName child in NCBI XML).
      - <Package>: ncbi_package
      - <Status>: status, status_date
      - All <Attribute> key-value pairs
    """
    import xml.etree.ElementTree as ET
    records = []
    root = ET.fromstring(xml_bytes)

    for sample in root.findall(".//BioSample"):
        record = {}

        # structural BioSample element attributes
        record["biosample_accession"] = sample.get("accession")
        record["biosample_id"]        = sample.get("id")
        record["submission_date"]     = sample.get("submission_date")
        record["last_update"]         = sample.get("last_update")
        record["publication_date"]    = sample.get("publication_date")
        record["access"]              = sample.get("access")

        # <Ids> block: SRA, sample name, rare BioProject in XML
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

        # <Description> block
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

        # <Organism> inside <Description>: name is in taxonomy_name attribute only.
        # No <OrganismName> child element exists in real NCBI BioSample XML.
        organism = sample.find(".//Organism")
        if organism is not None:
            record["taxonomy_id"]   = organism.get("taxonomy_id")
            record["taxonomy_name"] = organism.get("taxonomy_name")
            record["organism_name"] = organism.get("taxonomy_name")
        else:
            record["taxonomy_id"]   = None
            record["taxonomy_name"] = None
            record["organism_name"] = None

        # <Package> block
        package_el = sample.find(".//Package")
        record["ncbi_package"] = (
            package_el.text.strip()
            if package_el is not None and package_el.text
            else None
        )

        # <Status> block
        status_el = sample.find(".//Status")
        if status_el is not None:
            record["status"]      = status_el.get("status")
            record["status_date"] = status_el.get("when")
        else:
            record["status"]      = None
            record["status_date"] = None

        # all <Attribute> key-value pairs
        for attr in sample.findall(".//Attribute"):
            key = attr.get("harmonized_name") or attr.get("attribute_name", "unknown")
            val = (attr.text or "").strip()
            record[key] = val if val else None

        records.append(record)

    return records


def _download_file(url, dest_path):
    """Stream-download a large file to disk."""
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
