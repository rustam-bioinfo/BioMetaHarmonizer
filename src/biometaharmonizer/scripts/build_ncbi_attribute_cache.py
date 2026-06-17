"""
Fetches the NCBI BioSample attribute harmonization table and saves
ncbi_attributes.xml to src/biometaharmonizer/schemas/.

This file is Layer 2 of the synonym lookup used by synonyms.py and
consumed at runtime by both ingestion.py (Module 1) and key_mapper.py
(Module 2) via build_synonym_lookup(). It must be present for Layer 2
resolution to be active; without it the tool falls back to unified.json
(Layer 1) only.

Run once after cloning, and re-run periodically to pick up new NCBI
attribute definitions:

    biometaharmonizer build-ncbi-cache

Optional flags:
    --output-dir DIR   Write output to DIR instead of the default
                       src/biometaharmonizer/schemas/ path.
    --skip-fetch       Re-use an existing ncbi_attributes.xml and only
                       validate + report it (no network request).

Output:
    ncbi_attributes.xml   Raw NCBI attribute XML; parsed at runtime by
                          synonyms.build_synonym_lookup() for HarmonizedName
                          and Synonym resolution.
"""

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

NCBI_URL = "https://www.ncbi.nlm.nih.gov/biosample/docs/attributes/?format=xml"
MAX_ATTEMPTS = 3
TIMEOUT = 30

_DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


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
    raise RuntimeError(
        f"Failed to fetch NCBI attributes after {MAX_ATTEMPTS} attempts: {last_err}"
    )


def parse_and_report(xml_bytes: bytes) -> None:
    """Parse the XML and print a summary of what was found."""
    root = ET.fromstring(xml_bytes)
    harmonized_names = []
    total_synonyms = 0
    for attr in root.iter("Attribute"):
        hn_el = attr.find("HarmonizedName")
        if hn_el is None or not hn_el.text:
            continue
        harmonized_names.append(hn_el.text.strip())
        total_synonyms += sum(
            1 for s in attr.findall("Synonym") if s.text and s.text.strip()
        )
    print(f"  HarmonizedName entries : {len(harmonized_names)}")
    print(f"  Total Synonym entries  : {total_synonyms}")


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the NCBI BioSample attribute XML for synonym resolution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  biometaharmonizer build-ncbi-cache\n"
            "  biometaharmonizer build-ncbi-cache --output-dir /tmp/schemas\n"
            "  biometaharmonizer build-ncbi-cache --skip-fetch\n"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            f"Directory to write ncbi_attributes.xml into. "
            f"Defaults to {_DEFAULT_SCHEMAS_DIR}"
        ),
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help=(
            "Skip the network request and only validate/report an existing "
            "ncbi_attributes.xml in the output directory."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)

    schemas_dir = Path(args.output_dir) if args.output_dir else _DEFAULT_SCHEMAS_DIR
    schemas_dir.mkdir(parents=True, exist_ok=True)
    xml_path = schemas_dir / "ncbi_attributes.xml"

    if args.skip_fetch:
        if not xml_path.exists():
            print(
                f"[ERROR] --skip-fetch specified but {xml_path} does not exist.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[INFO] Using existing XML: {xml_path}")
        xml_bytes = xml_path.read_bytes()
    else:
        print(f"[INFO] Fetching NCBI BioSample attributes from:\n       {NCBI_URL}")
        xml_bytes = fetch_xml(NCBI_URL)
        xml_path.write_bytes(xml_bytes)
        print(f"[INFO] Saved: {xml_path}")

    print("[INFO] Parsing XML...")
    parse_and_report(xml_bytes)
    print("[INFO] Done. Run your pipeline -- synonyms.build_synonym_lookup() will")
    print(f"       pick up {xml_path.name} automatically as Layer 2.")


if __name__ == "__main__":
    main()
