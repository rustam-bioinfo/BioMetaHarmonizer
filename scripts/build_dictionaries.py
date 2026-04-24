#!/usr/bin/env python3
"""
build_dictionaries.py

Builds an enriched one_health_dictionaries.json by querying:
  1. OLS4 API  - ENVO, FoodOn, UBERON, Plant Ontology
  2. NCBI Entrez eutils - host_to_category for vertebrates + plants
  3. UMLS API  - synonym expansion (optional, requires API key)

The hand-curated base file is loaded first. Any key present in the base
always wins over ontology-derived data (merge strategy: base_wins).

Usage
-----
  python scripts/build_dictionaries.py \\
      --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \\
      --output src/biometaharmonizer/schemas/one_health_dictionaries.json \\
      --ncbi-key YOUR_NCBI_API_KEY           # optional, raises rate limit to 10 req/s
      --umls-key YOUR_UMLS_API_KEY           # optional

Dependencies (all standard or already in requirements.txt):
  requests>=2.28

The script is intentionally standalone - no imports from biometaharmonizer.
"""

import argparse
import json
import logging
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_dictionaries")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLS_BASE    = "https://www.ebi.ac.uk/ols4/api"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
UMLS_BASE   = "https://uts-ws.nlm.nih.gov/rest"

# Required by NCBI Entrez etiquette - set to something identifiable
NCBI_EMAIL = "biometaharmonizer@github"

# OLS4: map short IDs to full purl IRIs
OLS_IRI_PREFIXES = {
    "ENVO":   "http://purl.obolibrary.org/obo/ENVO_",
    "FOODON": "http://purl.obolibrary.org/obo/FOODON_",
    "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
    "PO":     "http://purl.obolibrary.org/obo/PO_",
}

# OLS ontology ids -> One Health category -> list of seed term IRIs
OLS_ONTOLOGY_MAP = {
    "envo": {
        "Environmental": [
            "ENVO:00000428",   # biome
            "ENVO:00010483",   # environmental material
            # ENVO:00002297 (environmental feature) is obsolete - replaced below
            "ENVO:01000254",   # anthropogenic environment
            "ENVO:01001110",   # ecosystem  (replaces obsolete ENVO:00002297)
            "ENVO:00005772",   # habitat    (replaces obsolete ENVO:00002297)
            "ENVO:00000063",   # soil
            "ENVO:00000015",   # ocean
            "ENVO:00000873",   # freshwater body
            "ENVO:00000134",   # sediment
            "ENVO:00002006",   # water
            "ENVO:00002042",   # surface water
            "ENVO:00000375",   # biofilm
        ],
    },
    "foodon": {
        "Food": [
            "FOODON:00001002",  # food product
            "FOODON:03400361",  # food material
            "FOODON:00001709",  # animal food product
            "FOODON:03310113",  # aquatic food product
            "FOODON:03411347",  # dairy product
            "FOODON:03420194",  # plant food product
        ],
    },
    "uberon": {
        "_anatomy": [
            "UBERON:0000465",  # material anatomical entity
        ],
    },
    "po": {
        "Plant": [
            "PO:0025131",  # plant anatomical entity
        ],
    },
}

# NCBI Taxonomy node IDs -> One Health category
NCBI_TAXON_ROOTS = {
    9606:  "Human",    # Homo sapiens (exact, no subtree walk)
    40674: "Animal",   # Mammalia
    8782:  "Animal",   # Aves
    8504:  "Animal",   # Reptilia
    8292:  "Animal",   # Amphibia
    7776:  "Animal",   # Chondrichthyes
    7898:  "Animal",   # Actinopterygii
    6656:  "Animal",   # Arthropoda
    6447:  "Animal",   # Mollusca
    33090: "Plant",    # Viridiplantae
    4751:  "Lab",      # Fungi
}

# Substring sets to classify UBERON anatomy terms
UBERON_HUMAN_EXCLUSIVE = {
    "cerebrospinal fluid", "pleural fluid", "peritoneal fluid",
    "synovial fluid", "amniotic fluid", "dialysate",
    "bronchoalveolar lavage", "sputum", "dental plaque",
    "catheter", "central venous",
}

UBERON_ANIMAL_EXCLUSIVE = {
    "rumen", "reticulum", "omasum", "abomasum",
    "gizzard", "proventriculus", "crop",
    "cloaca", "swim bladder", "gill",
    "hemolymph", "exoskeleton",
}

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# Set by parse_args() based on --ncbi-key presence:
#   0.34s  without API key  (3 req/s NCBI limit)
#   0.11s  with API key     (10 req/s NCBI limit, slight margin)
_NCBI_SLEEP = 0.34


def _get(url, params=None, retries=3, backoff=2.0, as_text=False):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = backoff * (attempt + 1) * 2
                log.warning("Rate limited, waiting %.0fs", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text if as_text else r.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                log.error("Failed GET %s: %s", url, exc)
                return None
            time.sleep(backoff)
    return None


# ---------------------------------------------------------------------------
# OLS4 helpers
# ---------------------------------------------------------------------------

def _short_id_to_iri(short_id):
    """
    Convert 'ENVO:00000428' -> 'http://purl.obolibrary.org/obo/ENVO_00000428'
    """
    prefix, local = short_id.split(":", 1)
    base_iri = OLS_IRI_PREFIXES.get(prefix.upper())
    if not base_iri:
        raise ValueError(f"Unknown IRI prefix: {prefix}")
    return base_iri + local


def ols_descendants(ontology, short_id, max_terms=2000):
    """
    Fetch all hierarchicalDescendant term labels + exact synonyms for a
    given short ID from OLS4.

    OLS4 endpoint:
      GET /ontologies/{onto}/terms/{double_encoded_iri}/hierarchicalDescendants

    IRI must be double URL-encoded as a path segment.
    Response: _embedded.terms[].label  +  annotation.hasExactSynonym[]
    """
    iri = _short_id_to_iri(short_id)
    encoded = quote(quote(iri, safe=""), safe="")
    url = f"{OLS_BASE}/ontologies/{ontology}/terms/{encoded}/hierarchicalDescendants"

    terms = []
    page = 0
    page_size = 500

    while len(terms) < max_terms:
        data = _get(url, params={"size": page_size, "page": page})
        if not data:
            break

        embedded = data.get("_embedded", {})
        items = embedded.get("terms", [])
        if not items:
            break

        for item in items:
            label = item.get("label", "")
            if label:
                terms.append(label.lower())

            # OLS4 synonyms live under annotation or oboInOwl:hasExactSynonym
            annotation = item.get("annotation", {})
            for syn_list in (
                annotation.get("hasExactSynonym", []),
                annotation.get("has_exact_synonym", []),
                item.get("synonyms") or [],
            ):
                for syn in syn_list:
                    if syn and isinstance(syn, str):
                        terms.append(syn.lower())

        page_info = data.get("page", {})
        total_pages = page_info.get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
        time.sleep(0.15)

    return list(dict.fromkeys(terms))


def fetch_ols_terms():
    log.info("Fetching OLS4 terms...")
    result = {}
    anatomy_all = []

    for ontology, category_map in OLS_ONTOLOGY_MAP.items():
        for category, short_ids in category_map.items():
            for short_id in short_ids:
                if category == "_anatomy":
                    log.info("  UBERON %s (anatomy)", short_id)
                    terms = ols_descendants(ontology, short_id, max_terms=3000)
                    anatomy_all.extend(terms)
                    log.info("    -> %d anatomy terms", len(terms))
                else:
                    log.info("  %s %s -> %s", ontology.upper(), short_id, category)
                    terms = ols_descendants(ontology, short_id)
                    result.setdefault(category, []).extend(terms)
                    log.info("    -> %d terms", len(terms))

    # Classify anatomy terms into human / animal / ambiguous
    human_terms, animal_terms, ambiguous_terms = [], [], []
    for term in anatomy_all:
        if any(excl in term for excl in UBERON_HUMAN_EXCLUSIVE):
            human_terms.append(term)
        elif any(excl in term for excl in UBERON_ANIMAL_EXCLUSIVE):
            animal_terms.append(term)
        else:
            ambiguous_terms.append(term)

    result["_uberon_human"]     = human_terms
    result["_uberon_animal"]    = animal_terms
    result["_uberon_ambiguous"] = ambiguous_terms

    return result


# ---------------------------------------------------------------------------
# NCBI Entrez helpers
# ---------------------------------------------------------------------------

def entrez_fetch_names(tax_ids, ncbi_key=None):
    """
    Fetch scientific + common names for a list of tax_ids via efetch.
    Returns list of (tax_id, sci_name, common_name) tuples.
    """
    if not tax_ids:
        return []

    results = []
    chunk_size = 500
    for i in range(0, len(tax_ids), chunk_size):
        chunk = tax_ids[i:i + chunk_size]
        params = {
            "db":      "taxonomy",
            "id":      ",".join(str(t) for t in chunk),
            "rettype": "xml",
            "retmode": "xml",
            "email":   NCBI_EMAIL,
        }
        if ncbi_key:
            params["api_key"] = ncbi_key
        xml_text = _get(f"{EUTILS_BASE}/efetch.fcgi", params=params, as_text=True)
        if not xml_text:
            continue
        try:
            root = ET.fromstring(xml_text)
            for taxon in root.findall(".//Taxon"):
                tax_id_el   = taxon.find("TaxId")
                sci_name_el = taxon.find("ScientificName")
                common_el   = taxon.find("OtherNames/CommonName")
                if tax_id_el is None or sci_name_el is None:
                    continue
                tid      = int(tax_id_el.text)
                sci_name = sci_name_el.text.lower().strip()
                common   = common_el.text.lower().strip() if common_el is not None else ""
                results.append((tid, sci_name, common))
        except ET.ParseError as exc:
            log.warning("XML parse error for chunk starting %s: %s", chunk[0], exc)
        time.sleep(_NCBI_SLEEP)

    return results


def fetch_all_names_under_taxon(taxon_id, category, ncbi_key=None, max_ids=10000):
    """
    Fetch all names in the subtree of taxon_id using a single esearch
    txid[Subtree] query followed by batched efetch.
    Returns dict: name_lower -> category
    """
    params = {
        "db":      "taxonomy",
        "term":    f"txid{taxon_id}[Subtree]",
        "retmax":  max_ids,
        "retmode": "json",
        "email":   NCBI_EMAIL,
    }
    if ncbi_key:
        params["api_key"] = ncbi_key

    data = _get(f"{EUTILS_BASE}/esearch.fcgi", params=params)
    if not data:
        return {}

    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        log.warning("  taxon %d: esearch returned no ids", taxon_id)
        return {}

    log.info("  taxon %d (%s): %d ids from esearch, fetching names...", taxon_id, category, len(ids))
    time.sleep(_NCBI_SLEEP)

    names = entrez_fetch_names([int(x) for x in ids], ncbi_key=ncbi_key)
    host_map = {}
    for _, sci_name, common_name in names:
        if sci_name:
            host_map[sci_name] = category
        if common_name:
            host_map[common_name] = category

    return host_map


def fetch_ncbi_host_map(ncbi_key=None):
    """
    Build host_to_category map using esearch txid[Subtree] for each root
    in NCBI_TAXON_ROOTS. Two API calls per taxon instead of N² elink calls.
    Returns dict: name_lower -> category
    """
    log.info("Fetching NCBI Taxonomy host mappings (esearch subtree)...")
    host_map = {
        "homo sapiens": "Human",
        "human":        "Human",
    }

    for root_id, category in NCBI_TAXON_ROOTS.items():
        if root_id == 9606:
            continue
        partial = fetch_all_names_under_taxon(root_id, category, ncbi_key=ncbi_key)
        host_map.update(partial)
        log.info("    -> %d names collected", len(partial))
        time.sleep(_NCBI_SLEEP)

    return host_map


# ---------------------------------------------------------------------------
# UMLS helpers (optional)
# ---------------------------------------------------------------------------

def umls_get_tgt(api_key):
    r = requests.post(
        "https://utslogin.nlm.nih.gov/cas/v1/api-key",
        data={"apikey": api_key},
        timeout=15,
    )
    r.raise_for_status()
    import re
    m = re.search(r'action="(.*?)"', r.text)
    if not m:
        raise RuntimeError("Could not parse UMLS TGT")
    return m.group(1)


def umls_service_ticket(tgt_url):
    r = requests.post(
        tgt_url,
        data={"service": "http://umlsks.nlm.nih.gov"},
        timeout=15,
    )
    r.raise_for_status()
    return r.text.strip()


def umls_synonyms_for_cui(cui, tgt_url):
    st   = umls_service_ticket(tgt_url)
    data = _get(
        f"{UMLS_BASE}/content/current/CUI/{cui}/atoms",
        params={"ticket": st, "language": "ENG", "pageSize": 100},
    )
    if not data:
        return []
    return [
        atom["name"].lower()
        for atom in data.get("result", [])
        if atom.get("name")
    ]


UMLS_SPECIMEN_CUIS = {
    "C0005767": "blood",
    "C0042036": "urine",
    "C0038569": "sputum",
    "C0007555": "cerebrospinal fluid",
    "C0205189": "pleural fluid",
    "C0003967": "ascitic fluid",
    "C0039981": "synovial fluid",
    "C0006252": "bronchial lavage",
    "C0444941": "wound",
    "C0000735": "abscess",
    "C0032227": "pus",
    "C0015411": "feces",
    "C0521481": "rectal swab",
    "C0029001": "oral swab",
    "C0042048": "vaginal swab",
    "C0877612": "nasal swab",
    "C0586478": "throat swab",
}


def fetch_umls_synonyms(api_key):
    log.info("Fetching UMLS synonyms...")
    tgt_url     = umls_get_tgt(api_key)
    synonym_map = {}
    for cui, canonical in UMLS_SPECIMEN_CUIS.items():
        syns = umls_synonyms_for_cui(cui, tgt_url)
        if syns:
            synonym_map[canonical] = syns
            log.info("  %s (%s): %d synonyms", canonical, cui, len(syns))
        time.sleep(0.1)
    return synonym_map


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms):
    log.info("Merging ontology data into base dictionary...")

    # ontology_map
    ont_map = base.setdefault("ontology_map", {})
    for category in ("Environmental", "Food", "Plant"):
        existing  = set(ont_map.get(category, []))
        new_terms = [
            t for t in ols_terms.get(category, [])
            if t not in existing and len(t) >= 3
        ]
        if new_terms:
            ont_map.setdefault(category, []).extend(new_terms)
            log.info("  ontology_map[%s] +%d terms", category, len(new_terms))

    # host_to_category
    host_map   = base.setdefault("host_to_category", {})
    base_hosts = {k.lower() for k in host_map}
    added      = sum(
        1 for name, cat in ncbi_host_map.items()
        if name.lower() not in base_hosts
        and not host_map.update({name.lower(): cat})
    )
    log.info("  host_to_category +%d entries", added)

    # UBERON anatomy -> unambiguous / ambiguous lists
    base_unambig_h = {t.lower() for t in base.get("unambiguous_human_terms",  [])}
    base_unambig_a = {t.lower() for t in base.get("unambiguous_animal_terms", [])}
    base_ambig     = {t.lower() for t in base.get("ambiguous_specimen_terms", [])}
    protected      = base_unambig_h | base_unambig_a | base_ambig

    new_h = [t for t in ols_terms.get("_uberon_human",     []) if t not in protected and 2 <= len(t) <= 60]
    new_a = [t for t in ols_terms.get("_uberon_animal",    []) if t not in protected and 2 <= len(t) <= 60]
    new_b = [t for t in ols_terms.get("_uberon_ambiguous", []) if t not in protected and 2 <= len(t) <= 60 and len(t.split()) <= 3]

    base.setdefault("unambiguous_human_terms",  []).extend(new_h)
    base.setdefault("unambiguous_animal_terms", []).extend(new_a)
    base.setdefault("ambiguous_specimen_terms", []).extend(new_b)
    log.info("  unambiguous_human_terms  +%d", len(new_h))
    log.info("  unambiguous_animal_terms +%d", len(new_a))
    log.info("  ambiguous_specimen_terms +%d", len(new_b))

    # UMLS synonym_map
    if umls_synonyms:
        syn_map       = base.setdefault("synonym_map", {})
        base_syn_keys = {k.lower() for k in syn_map}
        added_syns    = 0
        for canonical, synonyms in umls_synonyms.items():
            for syn in synonyms:
                s = syn.lower().strip()
                if s and s not in base_syn_keys and s != canonical.lower() and 3 <= len(s) <= 80:
                    syn_map[s] = canonical
                    base_syn_keys.add(s)
                    added_syns += 1
        log.info("  synonym_map +%d entries from UMLS", added_syns)

    return base


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def attach_metadata(data, args, ncbi_count, ols_counts):
    data["_metadata"] = {
        "build_date":   datetime.now(timezone.utc).isoformat(),
        "build_script": "scripts/build_dictionaries.py",
        "sources": {
            "hand_curated_base":       str(args.base),
            "ols4_api":                OLS_BASE,
            "ols4_ontologies":         list(OLS_ONTOLOGY_MAP.keys()),
            "ols4_term_counts":        ols_counts,
            "ncbi_entrez_api":         EUTILS_BASE,
            "ncbi_host_entries_added": ncbi_count,
            "umls_api":                UMLS_BASE if args.umls_key else "skipped",
        },
        "merge_strategy": "base_wins",
        "note": (
            "Hand-curated entries always override ontology-derived entries. "
            "Rebuild by running scripts/build_dictionaries.py."
        ),
    }
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Build enriched one_health_dictionaries.json from ontology sources."
    )
    p.add_argument("--base",      default="src/biometaharmonizer/schemas/one_health_dictionaries.json")
    p.add_argument("--output",    default="src/biometaharmonizer/schemas/one_health_dictionaries.json")
    p.add_argument("--ncbi-key",  default=None, help="NCBI API key (raises rate limit to 10 req/s)")
    p.add_argument("--umls-key",  default=None, help="UMLS API key for synonym expansion")
    p.add_argument("--skip-ols",  action="store_true")
    p.add_argument("--skip-ncbi", action="store_true")
    p.add_argument("--dry-run",   action="store_true")
    return p.parse_args()


def main():
    global _NCBI_SLEEP

    args      = parse_args()
    base_path = Path(args.base)

    if args.ncbi_key:
        _NCBI_SLEEP = 0.11
        log.info("NCBI API key provided - using 10 req/s rate limit")

    if not base_path.exists():
        log.error("Base file not found: %s", base_path)
        sys.exit(1)

    with open(base_path, "r", encoding="utf-8") as f:
        base = json.load(f)
    log.info("Loaded base dictionary from %s", base_path)

    ols_terms  = {}
    ols_counts = {}
    if not args.skip_ols:
        ols_terms = fetch_ols_terms()
        for cat, terms in ols_terms.items():
            if not cat.startswith("_"):
                ols_counts[cat] = len(terms)
                log.info("OLS terms collected: %s -> %d", cat, len(terms))
    else:
        log.info("Skipping OLS4 (--skip-ols)")

    ncbi_host_map = {}
    if not args.skip_ncbi:
        ncbi_host_map = fetch_ncbi_host_map(ncbi_key=args.ncbi_key)
        log.info("NCBI host map: %d entries total", len(ncbi_host_map))
    else:
        log.info("Skipping NCBI Taxonomy (--skip-ncbi)")

    umls_synonyms = {}
    if args.umls_key:
        try:
            umls_synonyms = fetch_umls_synonyms(args.umls_key)
        except Exception as exc:
            log.warning("UMLS fetch failed: %s", exc)
    else:
        log.info("Skipping UMLS (no --umls-key provided)")

    enriched = merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms)
    attach_metadata(enriched, args, len(ncbi_host_map), ols_counts)

    log.info("--- Final dictionary stats ---")
    for section, val in enriched.items():
        if section == "_metadata":
            continue
        if isinstance(val, dict):
            log.info("  %-35s  %d keys",    section, len(val))
        elif isinstance(val, list):
            log.info("  %-35s  %d entries", section, len(val))

    if args.dry_run:
        log.info("Dry run - output not written.")
        return

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=4, ensure_ascii=False)
    log.info("Written to %s", out_path)


if __name__ == "__main__":
    main()
