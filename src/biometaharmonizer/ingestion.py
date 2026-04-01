import pandas as pd
import requests
from pathlib import Path


def ingest(source, accessions=None):
    """
    Module 1: Data Ingestion.
    Accepts either a file path (TSV/CSV) or a list of BioSample accessions.
    Returns a flattened pandas DataFrame.
    """
    if accessions:
        return _fetch_from_ncbi(accessions)
    path = Path(source)
    if path.suffix in [".tsv", ".txt"]:
        return pd.read_csv(path, sep="\t", low_memory=False)
    elif path.suffix == ".csv":
        return pd.read_csv(path, low_memory=False)
    else:
        raise ValueError(f"Unsupported file format: {path.suffix}. Use TSV or CSV.")


def _fetch_from_ncbi(accessions):
    """
    Placeholder for NCBI Datasets API fetcher.
    To be implemented in Phase 3.
    """
    raise NotImplementedError("NCBI API fetcher not yet implemented. Provide a TSV/CSV file.")
