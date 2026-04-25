# Architecture

**Last updated:** 2026-04-25

## Overview

BioMetaHarmonizer is structured as a pipeline of independent engines, each responsible for one normalisation task.
All engines are stateless functions — they receive raw text and return a normalised value or `None`.
The pipeline is orchestrated by `ingestion.py` and exposed to the user through `cli.py`.

```
User / API
    │
    ▼
 cli.py  ──────────────────────────────────────────────────────────────┐
    │                                                                   │
    ▼                                                                   │
 ingestion.py                                                           │
    │  fetch_biosample_records()  — NCBI eutils efetch (XML)           │
    │  fetch_bioproject_meta()    — NCBI eutils esummary (JSON)        │
    │  normalise_record()         — calls all engines per record       │
    │                                                                   │
    ├──▶ key_mapper.py      canonicalise field names                   │
    ├──▶ date_engine.py     parse / normalise collection_date          │
    ├──▶ geo_engine.py      parse / normalise geo_loc_name             │
    ├──▶ one_health.py      classify host / isolation_source / env     │
    │       └── loads one_health_dictionaries.json at import time      │
    └──▶ output.py          write TSV + JSON                           │
                                                                        │
    ◀───────────────────────────────────────────────────────────────────┘
```

## Modules

### `cli.py`

Argparse entry point. Validates inputs, instantiates the pipeline, writes outputs.
Accepts `--bioproject`, `--biosample`, `--accession-list`, `--output`, and format flags.

### `ingestion.py`

All communication with the NCBI Entrez API lives here.

- `fetch_biosample_records(accessions)` — batched `efetch` calls, parses XML into a flat dict per record.
- `fetch_bioproject_meta(bioproject_id)` — retrieves project-level metadata.
- `normalise_record(record, engines)` — applies each engine in sequence; engines are injected so they can be swapped in tests.

Rate limiting: 3 req/s without an API key, 10 req/s with `NCBI_API_KEY` environment variable.

### `one_health.py`

One Health category classifier.
Loads `one_health_dictionaries.json` once at module import and builds in-memory lookup structures.

Classification priority (highest wins):

1. `host_to_category` — exact lookup of the `host` field after lowercasing and whitespace normalisation
2. `ontology_map` — keyword matching against `isolation_source` / `env_biome` / `env_material`
3. `unambiguous_human_terms` / `unambiguous_animal_terms` — anatomical term signals
4. `ambiguous_specimen_terms` — specimen terms that require corroborating signals
5. `ambiguous_category_terms` — terms present in multiple categories; resolved by voting across all available fields

See [build_dictionaries.md](build_dictionaries.md) for how each section of the dictionary is populated.

### `date_engine.py`

Parses `collection_date` free text into ISO 8601 (`YYYY-MM-DD`, `YYYY-MM`, or `YYYY`).
Handles: slash-delimited dates, month abbreviations, partial dates, range strings (`2015/2016` → `2015`).
Returns `None` for unparseable values and logs a warning.

### `geo_engine.py`

Normalises `geo_loc_name` to `Country: Region` format.
Applies a curated alias table (e.g. `"USA"` → `"United States"`), strips GPS coordinates accidentally placed in the field, and resolves common misspellings.

### `key_mapper.py`

Maps non-standard BioSample attribute names to canonical field names.
Example: `"host organism"`, `"host species"`, `"organism"` → `"host"`.
Uses a hand-curated alias dictionary; order-of-preference is preserved.

### `synonyms.py`

Synonym expansion used by `one_health.py` for specimen terms.
Loads the `synonym_map` section of `one_health_dictionaries.json` and provides a `resolve(term)` function that returns the canonical form or the original string unchanged.

### `output.py`

Writes the harmonised record list as:
- TSV — one row per BioSample, columns = canonical field names
- JSON — array of normalised record dicts, with `_metadata` block

## Data flow example

```
Input: BioProject PRJNA12345

1. ingestion.fetch_bioproject_meta()  → project title, organism, submission date
2. ingestion.fetch_biosample_records() → list of raw attribute dicts
3. For each record:
   a. key_mapper  → {"host": "Sus scrofa domesticus", "collection_date": "2019-Jul", ...}
   b. date_engine → collection_date = "2019-07"
   c. geo_engine  → geo_loc_name = "China: Guangdong"
   d. one_health  → one_health_category = "Animal"  (via host_to_category)
4. output.write_tsv() + output.write_json()
```

## Dictionary rebuild

The classification dictionary is a compiled artefact — it is not generated at runtime.
When ontology content needs to be updated, run `scripts/build_dictionaries.py`.
See [build_dictionaries.md](build_dictionaries.md) for the full procedure.
