# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-0.2.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)

A universal Python package for harmonizing, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

## Overview

The NCBI BioSample database is the central repository for genomic metadata. Because submissions are predominantly free-text and crowd-sourced, the metadata is highly unstructured and chaotic across thousands of submitters. **BioMetaHarmonizer** resolves this by providing a lightweight, pip-installable pipeline that:

- Fetches BioSample XML records directly from NCBI Entrez for any list of BioSample or assembly accessions
- Resolves BioProject accessions from NCBI assembly summary flat files
- Maps 300+ raw free-text attribute variants to a set of 29 standard keys via synonym lookup and fuzzy matching
- Parses dates (40+ formats), resolves ISO-3166 country/region geography, and classifies isolation source into One Health categories (human / animal / environment / food)
- Drops submitter-artifact columns (person names used as keys, one-off fields with <5 records)
- Validates mandatory field completeness per NCBI submission package

## Installation

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

## Quick Start

```python
import sys, os
sys.path.insert(0, "/content/BioMetaHarmonizer/src")
os.chdir("/content")  # assembly summary files download here

from biometaharmonizer.ingestion import ingest, set_email
from biometaharmonizer.key_mapper import KeyMapper
from biometaharmonizer.date_engine import DateEngine
from biometaharmonizer.geo_engine import GeoEngine
from biometaharmonizer.one_health import OneHealthClassifier

# 1. Ingest — accepts BioSample IDs, assembly accessions, or mixed list
set_email("your@email.com")
df = ingest("accessions.txt")         # or a Python list

# 2. Harmonize column names
mapper = KeyMapper()                   # loads schemas/unified.json automatically
df = mapper.map_columns(df)            # drop_sparse=5, drop_junk=True by default

# 3. Parse dates
de = DateEngine()
df["collection_date"] = de.parse_series(df["collection_date"])

# 4. Resolve geography
ge = GeoEngine()
df[["country", "region"]] = ge.resolve_series(df["geo_loc_name"])

# 5. Classify isolation source
oh = OneHealthClassifier()
df["one_health_category"] = oh.classify_series(df["isolation_source"])

print(df.shape)
df.to_csv("harmonized.csv", index=False)
```

## Architecture

```
BioMetaHarmonizer/
├── src/
│   └── biometaharmonizer/
│       ├── __init__.py             # version 0.2.0
│       ├── ingestion.py            # Module 1: Ingestion + BioProject resolution
│       ├── key_mapper.py           # Module 2: Synonym mapping + column cleaning
│       ├── date_engine.py          # Module 3: Temporal parsing (40+ formats)
│       ├── geo_engine.py           # Module 4: ISO-3166 geospatial resolution
│       └── one_health.py           # Module 5: One Health categorization
├── schemas/
│   ├── unified.json                # 29-field unified synonym lookup (all packages)
│   ├── mandatory_fields.json       # per-package mandatory field lists (20 packages)
│   ├── pathogen_cl_1.0.json        # legacy (no longer used by KeyMapper)
│   └── pathogen_env_1.0.json       # legacy (no longer used by KeyMapper)
├── tests/
│   ├── conftest.py
│   ├── test_ingestion.py
│   ├── test_key_mapper.py
│   ├── test_date_engine.py
│   ├── test_geo_engine.py
│   └── test_one_health.py
├── docs/
│   └── SESSION_PROTOCOL.md
├── setup.py
├── requirements.txt
and README.md
```

## Modules

| Module | File | Status | Notes |
|---|---|---|---|
| 1. Ingestion | `ingestion.py` | Complete | BioProject resolved via assembly summary flat files |
| 2. Key Harmonization | `key_mapper.py` | Complete | Unified schema, per-package mandatory validation |
| 3. Temporal Parsing | `date_engine.py` | Complete | 40+ date formats, ISO 8601 output |
| 4. Geospatial Resolution | `geo_engine.py` | Complete | ISO-3166 country + region |
| 5. One Health Categorization | `one_health.py` | Complete | Human / Animal / Environment / Food |

## Validated Performance

Validated on 6,618 *Bacillus cereus* group BioSample records (NCBI, 2025):

| Field | Fill rate |
|---|---|
| `biosample_accession` | 100% |
| `organism_name` | 95.8% |
| `bioproject_accession` | 96.4% |
| `collection_date` (parsed) | 79.3% |
| `geo_loc_name` — country | 94.9% |
| `geo_loc_name` — region | 58.8% |
| `one_health_category` | 76.6% |

Raw DataFrame shape after ingestion: 6,508 × 346.  
After `KeyMapper.map_columns()`: 6,508 × 195 (122 sparse + 23 junk columns removed).

## Schema Design

`schemas/unified.json` defines 29 standard keys with synonym lists covering all NCBI submission packages. All packages share the same synonym lookup — only their mandatory field requirements differ, defined in `schemas/mandatory_fields.json` for all 20 packages present in the validated dataset.

To extend: add new synonyms to `unified.json` or add a new package entry to `mandatory_fields.json`. No Python code changes required.

## Input Formats

| Format | Example |
|---|---|
| BioSample ID file | `SAMN12345678` one per line |
| Assembly accession file | `GCF_000001405.39` one per line |
| Mixed file | both types in same file |
| Python list | `ingest(["SAMN12345678", "GCF_000001405.39"])` |

## Running Tests

```bash
pytest tests/ -v --tb=short
pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing
```

## Target Publication

*Bioinformatics* (Application Note) or *Nucleic Acids Research* (Web Server / Software)

## License

MIT
