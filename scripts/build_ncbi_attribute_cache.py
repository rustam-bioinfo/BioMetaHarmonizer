"""
One-time script to fetch the NCBI BioSample attribute harmonization table,
parse all HarmonizedName / Synonym pairs, compute sentence-transformers embeddings,
and save the cache files used by KeyMapper.

Run once before using KeyMapper:
    python scripts/build_ncbi_attribute_cache.py

To use a different embedding model:
    python scripts/build_ncbi_attribute_cache.py --model BAAI/bge-small-en-v1.5

Supported models (any sentence-transformers model works; these are tested):
    all-MiniLM-L6-v2          (default, 384-dim, fast, small)
    all-MiniLM-L12-v2         (384-dim, slightly better quality)
    all-mpnet-base-v2         (768-dim, higher quality, slower)
    BAAI/bge-small-en-v1.5    (384-dim, strong retrieval)
    BAAI/bge-base-en-v1.5     (768-dim, strong retrieval)
    intfloat/e5-small-v2      (384-dim, E5 family)
    intfloat/e5-base-v2       (768-dim, E5 family)

Outputs (all written to src/biometaharmonizer/schemas/):
    ncbi_attributes.xml          -- raw NCBI XML
    ncbi_embeddings.npy          -- float32 array, shape [N, embedding_dim]
    ncbi_harmonized_names.json   -- sorted list of N harmonized names
    ncbi_cache_meta.json         -- build metadata (model name, dim, count, timestamp)
"""

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

NCBI_URL = "https://www.ncbi.nlm.nih.gov/biosample/docs/attributes/?format=xml"
MAX_ATTEMPTS = 3
TIMEOUT = 30
DEFAULT_MODEL = "all-MiniLM-L6-v2"

# Write cache files into the package schemas/ directory so they are found
# by importlib.resources regardless of install mode.
_SCHEMAS_DIR = Path(__file__).parent.parent / "src" / "biometaharmonizer" / "schemas"


def fetch_xml(url: str) -> bytes:
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            last_err = exc
            print(f"[WARNING] Attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch NCBI attributes after {MAX_ATTEMPTS} attempts: {last_err}")


def parse_attributes(xml_bytes: bytes) -> tuple[list[str], dict[str, list[str]]]:
    root = ET.fromstring(xml_bytes)
    harmonized_names: list[str] = []
    synonyms_map: dict[str, list[str]] = {}

    for attr in root.iter("Attribute"):
        hn_el = attr.find("HarmonizedName")
        if hn_el is None or not hn_el.text:
            continue
        hn = hn_el.text.strip()
        syns = [s.text.strip() for s in attr.findall("Synonym") if s.text and s.text.strip()]
        harmonized_names.append(hn)
        synonyms_map[hn] = syns

    harmonized_names = sorted(set(harmonized_names))
    return harmonized_names, synonyms_map


def build_embeddings(names: list[str], model_name: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    print(f"[INFO] Loading model: {model_name}")
    model = SentenceTransformer(model_name)
    print(f"[INFO] Encoding {len(names)} harmonized names...")
    embeddings = model.encode(names, normalize_embeddings=True, show_progress_bar=True)
    return embeddings.astype(np.float32)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the NCBI BioSample attribute cache for KeyMapper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/build_ncbi_attribute_cache.py\n"
            "  python scripts/build_ncbi_attribute_cache.py --model BAAI/bge-small-en-v1.5\n"
            "  python scripts/build_ncbi_attribute_cache.py --model all-mpnet-base-v2\n"
        ),
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=(
            f"sentence-transformers model to use for embeddings (default: {DEFAULT_MODEL}). "
            "Any model on HuggingFace Hub that works with sentence-transformers is accepted. "
            "The model name is recorded in ncbi_cache_meta.json so KeyMapper can load "
            "the same model automatically."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    model_name = args.model

    _SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)

    xml_path   = _SCHEMAS_DIR / "ncbi_attributes.xml"
    emb_path   = _SCHEMAS_DIR / "ncbi_embeddings.npy"
    names_path = _SCHEMAS_DIR / "ncbi_harmonized_names.json"
    meta_path  = _SCHEMAS_DIR / "ncbi_cache_meta.json"

    print(f"[INFO] Fetching NCBI BioSample attributes from:\n       {NCBI_URL}")
    xml_bytes = fetch_xml(NCBI_URL)

    xml_path.write_bytes(xml_bytes)
    print(f"[INFO] XML saved: {xml_path}")

    harmonized_names, synonyms_map = parse_attributes(xml_bytes)
    total_synonyms = sum(len(v) for v in synonyms_map.values())

    embeddings = build_embeddings(harmonized_names, model_name)

    np.save(str(emb_path), embeddings)
    names_path.write_text(json.dumps(harmonized_names, indent=2), encoding="utf-8")

    # Write metadata so KeyMapper can discover which model was used
    meta = {
        "model": model_name,
        "embedding_dim": int(embeddings.shape[1]),
        "num_names": len(harmonized_names),
        "num_synonyms": total_synonyms,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print()
    print(f"Model used:             {model_name}")
    print(f"Embedding dimensions:   {embeddings.shape[1]}")
    print(f"Total harmonized names: {len(harmonized_names)}")
    print(f"Total synonyms indexed: {total_synonyms}")
    print(f"Embeddings saved:       {emb_path}")
    print(f"XML cache saved:        {xml_path}")
    print(f"Metadata saved:         {meta_path}")


if __name__ == "__main__":
    main()
