# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-0.3.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)

A universal Python package for harmonizing, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

## Overview

The NCBI BioSample database is the central repository for genomic metadata. Because submissions are predominantly free-text and crowd-sourced, the metadata is highly unstructured across thousands of submitters. **BioMetaHarmonizer** resolves this by providing a lightweight, pip-installable pipeline that:

- Fetches BioSample XML records directly from NCBI Entrez for any list of BioSample or assembly accessions
- Resolves BioProject accessions from NCBI assembly summary flat files
- Maps raw free-text attribute variants to NCBI standard keys using the **official NCBI BioSample harmonization table** as primary authority (Layer 1) and **sentence-transformers semantic matching** as fallback (Layer 2) — no manual curation required
- Parses dates (40+ formats → ISO 8601), resolves ISO-3166 country/region geography, and classifies isolation source into One Health categories (Human / Animal / Food / Environmental / Lab)
- Drops submitter-artifact columns (person names used as keys, one-off fields with fewer than 5 records)
- Validates mandatory field completeness per NCBI submission package
- Writes harmonized output to CSV, TSV, Excel, or Parquet

## Installation

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

### Build the NCBI attribute cache (required once before first use)

```bash
# Default model (all-MiniLM-L6-v2, 384-dim, fast)
python scripts/build_ncbi_attribute_cache.py

# Or choose a different sentence-transformers model
python scripts/build_ncbi_attribute_cache.py --model BAAI/bge-small-en-v1.5
python scripts/build_ncbi_attribute_cache.py --model all-mpnet-base-v2
```

This fetches the NCBI BioSample harmonization table, indexes all synonyms, computes
embeddings for semantic fallback, and saves four files under `src/biometaharmonizer/schemas/`:

```
ncbi_attributes.xml          # raw NCBI XML (Layer 1 source)
ncbi_embeddings.npy          # float32 embeddings, shape [N, embedding_dim]
ncbi_harmonized_names.json   # sorted list of N harmonized names
ncbi_cache_meta.json         # build metadata: model name, dim, count, timestamp
```

The model name is recorded in `ncbi_cache_meta.json` so `KeyMapper` picks it up
automatically — no code changes needed when switching models.

Re-run this script at any time to refresh the cache against the latest NCBI attribute release.

## Quick Start

### Command line

```bash
biometaharmonizer run \
    --input  accessions.txt \
    --email  your@email.com \
    --output harmonized.csv
```

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--api-key KEY` | — | NCBI API key (raises rate limit to 10 req/s) |
| `--cache-dir DIR` | `~/.biometaharmonizer/cache/` | Assembly summary cache directory |
| `--format FORMAT` | inferred from extension | `csv`, `tsv`, `excel`, `parquet` (case-insensitive) |
| `--summary FILE` | — | Write per-column fill-rate CSV |
| `--model MODEL` | from cache metadata | sentence-transformers model for Layer 2 semantic matching; must match the model used to build `ncbi_embeddings.npy` |
| `--threshold FLOAT` | 0.75 | Cosine similarity threshold for Layer 2 acceptance (lower = more recall, less precision) |
| `--drop-sparse N` | 5 | Drop columns below this non-null threshold. An integer (e.g. `5`) is an absolute row count; a float between 0 and 1 (e.g. `0.05`) is a fractional fill rate and drops columns with less than that fraction of non-null values. Set to `0` to disable. |
| `--no-drop-junk` | off | Keep submitter-artifact columns (person names, emails) |
| `--skip-dates` | off | Skip ISO 8601 date parsing |
| `--skip-geo` | off | Skip geospatial resolution |
| `--skip-one-health` | off | Skip One Health classification |
| `--verbose` | off | Enable DEBUG logging |

### Python API

```python
from biometaharmonizer.ingestion import set_email, ingest
from biometaharmonizer import KeyMapper, DateEngine, GeoEngine, OneHealthClassifier, write, write_summary

# 1. Ingest — accepts BioSample IDs, assembly accessions, or a mixed file
set_email("your@email.com")
df = ingest("accessions.txt")

# 2. Harmonize column names (Layer 1: NCBI XML synonyms; Layer 2: semantic fallback)
mapper = KeyMapper()
df = mapper.map_columns(df)          # drop_sparse=5, drop_junk=True by default

# 3. Parse dates → ISO 8601 truncated (YYYY / YYYY-MM / YYYY-MM-DD)
de = DateEngine()
date_df = de.parse_with_range(df["collection_date"])
df["collection_date"] = date_df["collection_date"]

# 4. Resolve geography → snake_case columns
ge = GeoEngine()
geo = ge.parse(df["geo_loc_name"])   # returns DataFrame with geo_country, geo_region, ...
# Only join columns not already present to avoid collisions with raw input columns
new_geo_cols = [c for c in geo.columns if c not in df.columns]
df = df.join(geo[new_geo_cols])

# 5. Classify isolation source + host → One Health category
oh = OneHealthClassifier()
df["one_health_category"] = oh.classify_joint(df["isolation_source"], df["host"])

# 6. Write output
write(df, "harmonized.csv")
write_summary(df, "fill_rates.csv")

print(df.shape)
```

## Architecture

```
BioMetaHarmonizer/
├── src/biometaharmonizer/
│   ├── __init__.py             # version 0.3.0, full public API
│   ├── cli.py                  # CLI entrypoint: biometaharmonizer run
│   ├── ingestion.py            # Module 1: Ingestion + BioProject resolution
│   ├── key_mapper.py           # Module 2: NCBI XML (Layer 1) + sentence-transformers (Layer 2)
│   ├── date_engine.py          # Module 3: Temporal parsing (40+ formats → ISO 8601)
│   ├── geo_engine.py           # Module 4: ISO-3166 geospatial resolution
│   ├── one_health.py           # Module 5: One Health categorization (Tier 1 Regex)
│   └── output.py               # Module 6: Write CSV / TSV / Excel / Parquet
├── schemas/
│   ├── ncbi_attributes.xml             # NCBI official harmonization table (built by script)
│   ├── ncbi_embeddings.npy             # sentence-transformers embeddings, shape [N, dim], float32
│   ├── ncbi_harmonized_names.json      # sorted list of N harmonized names
│   ├── ncbi_cache_meta.json            # build metadata: model, dim, count, timestamp
│   ├── mandatory_fields.json           # per-package required field lists (22 packages)
│   ├── unified.json                    # legacy synonym lookup (v0.2.0, superseded)
│   ├── pathogen_cl_1.0.json            # legacy
│   └── pathogen_env_1.0.json           # legacy
├── scripts/
│   └── build_ncbi_attribute_cache.py   # one-time cache builder; --model flag to choose model
├── tests/
│   ├── conftest.py
│   ├── test_ingestion.py
│   ├── test_key_mapper.py
│   ├── test_date_engine.py
│   ├── test_geo_engine.py
│   ├── test_one_health.py
│   ├── test_output.py
│   └── test_pipeline.py
├── docs/SESSION_PROTOCOL.md
├── pyproject.toml
└── requirements.txt
```

## Modules

| Module | File | Status | Notes |
|---|---|---|---|
| 1. Ingestion | `ingestion.py` | Complete | BioProject resolved via assembly summary flat files |
| 2. Key Harmonization | `key_mapper.py` | Complete | NCBI XML (Layer 1) + sentence-transformers (Layer 2); rapidfuzz removed |
| 3. Temporal Parsing | `date_engine.py` | Complete | 40+ date formats, ISO 8601 output |
| 4. Geospatial Resolution | `geo_engine.py` | Complete | ISO-3166 country, region, locality |
| 5. One Health Categorization | `one_health.py` | Complete | Human / Animal / Food / Environmental / Lab |
| 6. Output | `output.py` | Complete | CSV, TSV, Excel, Parquet; fill-rate summary |

## Key Harmonization: How It Works

`KeyMapper` resolves raw submitter column names to NCBI standard keys using two layers:

**Layer 1 — NCBI attribute XML (authoritative)**
The [NCBI BioSample harmonization table](https://www.ncbi.nlm.nih.gov/biosample/docs/attributes/?format=xml)
maps every known submitter synonym to its canonical `HarmonizedName`. This covers the vast
majority of real-world column names without any manual curation.

**Layer 2 — Semantic fallback (sentence-transformers, configurable)**
Column names absent from the NCBI table are matched by cosine similarity against
precomputed embeddings of all harmonized names (default threshold: 0.75). The model
is configurable — any [sentence-transformers](https://sbert.net) model works:

```python
# Default: reads model from ncbi_cache_meta.json (all-MiniLM-L6-v2 unless rebuilt)
mapper = KeyMapper()

# Custom model (rebuild the cache with the same model first)
mapper = KeyMapper(model="BAAI/bge-small-en-v1.5", threshold=0.70)
```

| Model | Dim | Characteristics |
|-------|-----|-----------------|
| `all-MiniLM-L6-v2` | 384 | Default — fast, small |
| `all-MiniLM-L12-v2` | 384 | Slightly better quality |
| `all-mpnet-base-v2` | 768 | Higher quality, slower |
| `BAAI/bge-small-en-v1.5` | 384 | Strong retrieval |
| `BAAI/bge-base-en-v1.5` | 768 | Strong retrieval, larger |
| `intfloat/e5-small-v2` | 384 | E5 family |
| `intfloat/e5-base-v2` | 768 | E5 family, larger |

The model is loaded lazily on first use. Both layers are entirely config-driven — no
Python code changes are needed when NCBI adds new attributes or when new synonym
patterns appear in submissions.

## Geospatial Parsing

`GeoEngine` accepts the standard NCBI `geo_loc_name` field and splits it into four columns:
`geo_country`, `geo_region`, `geo_locality`, and `geo_iso3166` (ISO 3166-1 alpha-2 code).

Supported input formats:

| Input | Parsed as |
|---|---|
| `"USA: California, Los Angeles"` | country=USA, region=California, locality=Los Angeles |
| `"USA: California"` | country=USA, region=California |
| `"Germany, Bavaria"` | country=Germany, locality=Bavaria |
| `"France"` | country=France |
| `"Pacific Ocean"` | sea/ocean flag set, country columns left empty |
| Decimal coordinates | stored in `geo_loc_raw`, country columns left empty |

UK sub-country names (England, Scotland, Wales, Northern Ireland) are normalised to
`"United Kingdom"` with ISO code `GB`. Ambiguous `"Korea"` defaults to South Korea (`KR`)
with a warning logged.

## Input Formats

| Format | Example |
|---|---|
| BioSample ID file | `SAMN12345678` one per line |
| Assembly accession file | `GCF_000001405.39` one per line |
| Mixed file | both types in the same file |
| Python list | `ingest(["SAMN12345678", "GCF_000001405.39"])` |

## Output

```python
from biometaharmonizer import write, write_summary

write(df, "harmonized.csv")                  # default: CSV
write(df, "harmonized.xlsx", fmt="excel")    # Excel
write(df, "harmonized.parquet", fmt="parquet")

write_summary(df, "fill_rates.csv")          # column_name, non_null_count, fill_pct
```

Output format strings are case-insensitive (`"CSV"`, `"csv"`, and `"Csv"` are all accepted).

## Running Tests

```bash
pip install pytest
pytest tests/ -v --tb=short
pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing
```

All tests are self-contained (no live NCBI calls). Current: **170/170 passing**.

## Target Publication

*Bioinformatics* (Application Note), *GigaScience*, or *Microbial Genomics*

## License

MIT
