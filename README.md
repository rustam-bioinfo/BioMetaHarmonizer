# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-0.5.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)

A universal Python package for harmonizing, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

## Overview

The NCBI BioSample database is the central repository for genomic metadata. Because submissions are predominantly free-text and crowd-sourced, the metadata is highly unstructured across thousands of submitters. **BioMetaHarmonizer** resolves this by providing a lightweight, pip-installable pipeline that:

- Fetches BioSample XML records directly from NCBI Entrez for any list of BioSample or assembly accessions
- Resolves BioProject and assembly accessions (GCF_ / GCA_) from NCBI assembly summary flat files
- Produces a **fixed, deterministic output schema** — every run on any dataset outputs the same columns in the same order, regardless of what attributes individual submitters included
- Maps raw free-text attribute variants to standard keys using the **official NCBI BioSample `harmonized_name` XML attribute** as the primary signal, with a two-layer synonym lookup (unified.json + NCBI attribute table) as fallback
- Any attribute that does not resolve to a known final output column is preserved losslessly in `_extra_attributes` as a JSON string — no data is ever discarded
- Normalizes common null-like submitter values (`missing`, `not applicable`, `unknown`, `N/A`, `missing: ...`, etc.) to true missing values during ingestion so downstream parsing operates on clean metadata
- Parses dates (40+ formats → ISO 8601), resolves ISO-3166 country/region geography, and classifies isolation source into One Health categories (Human / Animal / Food / Environmental / Lab) — all in-place into the fixed schema columns
- Writes harmonized output to CSV, TSV, Excel, or Parquet

## Installation

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

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
| `--verbose` | off | Enable DEBUG logging |

### Python API

```python
from biometaharmonizer.ingestion import set_email, ingest
from biometaharmonizer import KeyMapper, DateEngine, GeoEngine, OneHealthClassifier, write, write_summary

# 1. Ingest — accepts BioSample IDs, assembly accessions, or a mixed file
#    Output is already a fixed-schema DataFrame: every column always present.
set_email("your@email.com")
df = ingest("accessions.txt")

# 2. Harmonize column names (for non-ingestion/custom workflows)
#    Renames raw columns to standard keys, coalesces any duplicates,
#    and reindexes to the fixed schema.
mapper = KeyMapper()
df = mapper.map_columns(df)

# 3. Parse dates → ISO 8601 truncated (YYYY / YYYY-MM / YYYY-MM-DD)
#    Written in-place into collection_date and collection_date_range columns.
de = DateEngine()
date_df = de.parse_with_range(df["collection_date"])
df["collection_date"] = date_df["collection_date"]
df["collection_date_range"] = date_df["collection_date_range"]

# 4. Resolve geography → in-place into geo_country, geo_region, etc.
ge = GeoEngine()
geo_df = ge.parse(df["geo_loc_name"])
for col in geo_df.columns:
    df[col] = geo_df[col]

# 5. Classify isolation source + host → in-place into one_health_category
oh = OneHealthClassifier()
df["one_health_category"] = oh.classify_joint(df["isolation_source"], df["host"])

# 6. Write output
write(df, "harmonized.csv")
write_summary(df, "fill_rates.csv")

print(df.shape)   # (N_records, fixed number of columns — always identical)
```

## Output Schema

Every output file contains exactly the following columns, in this order, regardless of dataset.
Attributes that are not part of the fixed schema are preserved in `_extra_attributes` as a JSON
string — including low-frequency NCBI attributes such as `antimicrobial_resistance`, `temp`, `ph`,
`depth`, `elev`, `samp_size`, and `samp_mat_process`.

| # | Column | Source | Description |
|---|--------|--------|-------------|
| 1 | `biosample_accession` | BioSample XML structural field | NCBI BioSample accession (e.g. `SAMN07597573`) — primary record identifier |
| 2 | `biosample_id` | BioSample XML structural field | NCBI internal numeric BioSample ID (e.g. `400804`) — differs from accession |
| 3 | `sra_accession` | BioSample XML structural field | Linked SRA run/experiment accession (e.g. `SRS123456`), if deposited |
| 4 | `bioproject_accession` | BioSample XML / assembly index | Parent BioProject accession (e.g. `PRJNA123456`); resolved from XML or assembly index |
| 5 | `assembly_accession_refseq` | Assembly index (GCF_) | RefSeq assembly accession (e.g. `GCF_000001405.39`); resolved from NCBI assembly index |
| 6 | `assembly_accession_genbank` | Assembly index (GCA_) | GenBank assembly accession (e.g. `GCA_000001405.29`); resolved from NCBI assembly index |
| 7 | `sample_name_id` | BioSample XML structural field | Submitter-assigned sample name or lab ID (from `<Id db_label="Sample name">`) |
| 8 | `taxonomy_id` | BioSample XML structural field | NCBI Taxonomy numeric ID (taxid) for the organism (e.g. `1396` for *Bacillus cereus*) |
| 9 | `taxonomy_name` | BioSample XML structural field | Taxon name stored by NCBI for the assigned `taxonomy_id`; reflects the name of that specific taxid, which for strain-level entries may include strain designators (e.g. `Bacillus cereus NC7401`); use `taxonomy_id` for reliable grouping |
| 10 | `organism_name` | BioSample XML structural field | Organism name as written by the submitter in `<OrganismName>`; may include strain designations or extra qualifiers; falls back to `taxonomy_name` if absent; generally noisier but sometimes more informative than `taxonomy_name` at species level |
| 11 | `collection_date` | BioSample attribute → DateEngine | Normalized collection date in ISO 8601 format (YYYY, YYYY-MM, or YYYY-MM-DD) |
| 12 | `collection_date_range` | DateEngine output | Inferred date range when the submitter provided a year or year-month (e.g. `2014-01-01/2014-12-31` for `2014`) |
| 13 | `geo_loc_name` | BioSample attribute | Raw geographic location string as submitted (e.g. `USA: IA`) |
| 14 | `lat_lon` | BioSample attribute | Decimal latitude/longitude as submitted after null normalization; null-like values such as `missing`, `unknown`, and `not applicable` are converted to missing |
| 15 | `geo_country` | GeoEngine output | Standardized country name resolved from `geo_loc_name`; historical names are preserved as submitted |
| 16 | `geo_region` | GeoEngine output | Sub-national region (state, province, oblast) resolved from `geo_loc_name` |
| 17 | `geo_locality` | GeoEngine output | City, locality, or marine sub-location resolved from `geo_loc_name` |
| 18 | `geo_iso3166` | GeoEngine output | ISO 3166-1 alpha-2 country code (e.g. `US`, `DE`, `GB`); historical/defunct country names are tagged as `HISTORICAL` |
| 19 | `geo_sea_ocean` | GeoEngine output | Sea or ocean name if `geo_loc_name` refers to a marine location (e.g. `Pacific Ocean`) |
| 20 | `geo_loc_raw` | GeoEngine output | Preserved raw value when `geo_loc_name` contains coordinates only and no named place could be resolved |
| 21 | `host` | BioSample attribute → OneHealthClassifier | Host organism name as submitted (e.g. `Homo sapiens`, `Zea mays`, `Gallus gallus`) |
| 22 | `host_disease` | BioSample attribute | Disease associated with the host at time of sampling |
| 23 | `host_age` | BioSample attribute | Age of the host at time of sampling |
| 24 | `host_sex` | BioSample attribute | Biological sex of the host |
| 25 | `host_tissue_sampled` | BioSample attribute | Tissue or body site from which the sample was taken |
| 26 | `isolation_source` | BioSample attribute → OneHealthClassifier | Free-text description of the material or environment from which the isolate was obtained |
| 27 | `sample_type` | BioSample attribute | Sample type or specimen classification as submitted |
| 28 | `one_health_category` | OneHealthClassifier output | One Health classification inferred from `isolation_source` and `host`: Human, Animal, Food, Environmental, or Lab |
| 29 | `isolate` | BioSample attribute | Isolate identifier or name assigned by the submitter |
| 30 | `strain` | BioSample attribute | Microbial strain designation as submitted (e.g. `ATCC 14579`, `H37Rv`) |
| 31 | `sub_strain` | BioSample attribute | Sub-strain designation, if applicable |
| 32 | `serotype` | BioSample attribute | Serotype designation (e.g. `O157:H7`) |
| 33 | `serovar` | BioSample attribute | Serovar designation, used primarily for *Salmonella* and similar organisms |
| 34 | `genotype` | BioSample attribute | Genotype or sequence type designation (e.g. ST11, cgST) |
| 35 | `culture_collection` | BioSample attribute | Culture collection identifier (e.g. `ATCC 14579`) |
| 36 | `outbreak` | BioSample attribute | Outbreak identifier or name associated with the isolate, if any |
| 37 | `env_broad_scale` | BioSample attribute | Broad environmental context (ENVO term), used mainly for environmental/metagenomics samples |
| 38 | `env_local_scale` | BioSample attribute | Local environmental feature (ENVO term) |
| 39 | `env_medium` | BioSample attribute | Environmental medium from which the sample was taken (ENVO term, e.g. soil, water) |
| 40 | `sequencing_method` | BioSample attribute | Sequencing platform or technology (e.g. `Illumina HiSeq`, `Oxford Nanopore`) |
| 41 | `assembly_method` | BioSample attribute | Genome assembly software and version (e.g. `SPAdes 3.15`) |
| 42 | `collected_by` | BioSample attribute; `<Owner/Name>` fallback | Person or organization that physically collected the sample; explicit BioSample attribute is always preferred over submission owner |
| 43 | `ncbi_package` | BioSample XML structural field | NCBI BioSample package name defining the required attribute set (e.g. `Microbe.1.0`) |
| 44 | `submission_date` | BioSample XML structural field | Date the BioSample record was first submitted to NCBI |
| 45 | `last_update` | BioSample XML structural field | Date the BioSample record was last modified |
| 46 | `publication_date` | BioSample XML structural field | Date the BioSample record was made publicly available |
| 47 | `access` | BioSample XML structural field | Access level of the record (`public` or `controlled-access`) |
| 48 | `status` | BioSample XML structural field | Current record status (e.g. `live`, `suppressed`) |
| 49 | `status_date` | BioSample XML structural field | Date the current status was assigned |
| 50 | `title` | BioSample XML structural field | Free-text title of the BioSample record as submitted |
| 51 | `description_comment` | BioSample XML structural field | Free-text description or comment block from the BioSample record |
| 52 | `_extra_attributes` | JSON dict of all unresolved submitter attributes | All attributes that could not be mapped to a schema column, serialized as JSON; also contains `submission_owner` and `submission_contact` when both an explicit collector and an `<Owner>` block are present |

Columns that have no data for a given dataset are present but filled with `NaN`. No columns are ever added, dropped, or reordered at runtime.

## Architecture

```text
BioMetaHarmonizer/
├── src/biometaharmonizer/
│   ├── __init__.py             # version 0.5.0, full public API
│   ├── cli.py                  # CLI entrypoint: biometaharmonizer run
│   ├── ingestion.py            # Module 1: fixed schema, XML parsing, BioProject resolution
│   ├── synonyms.py             # Shared two-layer synonym lookup (unified.json + NCBI XML)
│   ├── key_mapper.py           # Module 2: column rename, coalesce, reindex to fixed schema
│   ├── date_engine.py          # Module 3: temporal parsing (40+ formats → ISO 8601)
│   ├── geo_engine.py           # Module 4: ISO-3166 geospatial resolution
│   ├── one_health.py           # Module 5: One Health categorization (Tier 1 Regex)
│   ├── output.py               # Module 6: write CSV / TSV / Excel / Parquet
│   └── schemas/
│       ├── ncbi_attributes.xml         # NCBI official harmonization table (Layer 2, optional)
│       └── unified.json                # standard_key definitions + synonym lists (Layer 1)
├── scripts/
│   └── build_ncbi_attribute_cache.py   # optional: rebuild ncbi_attributes.xml from NCBI
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
| 1. Ingestion | `ingestion.py` | Complete | Fixed schema defined here; BioProject and assembly accessions resolved via assembly index; shared null normalization applied across all attributes |
| Synonym Lookup | `synonyms.py` | Complete | Single shared two-layer lookup used by ingestion + key_mapper; result cached per process |
| 2. Key Harmonization | `key_mapper.py` | Complete | Rename raw columns to standard keys, coalesce duplicates, reindex to fixed schema |
| 3. Temporal Parsing | `date_engine.py` | Complete | 40+ date formats, ISO 8601 output |
| 4. Geospatial Resolution | `geo_engine.py` | Complete | ISO-3166 country, region, locality, oceans; historical names tagged as `HISTORICAL` |
| 5. One Health Categorization | `one_health.py` | Complete | Human / Animal / Food / Environmental / Lab |
| 6. Output | `output.py` | Complete | CSV, TSV, Excel, Parquet; fill-rate summary |

## Attribute Resolution: How It Works

When parsing a live BioSample XML record, each `<Attribute>` element is resolved in the following priority order:

1. **NCBI `harmonized_name` (authoritative)** — if the `harmonized_name` attribute in the XML element matches a known final output column directly, it is used without any synonym lookup. This is the signal NCBI itself assigns and is the most reliable mapping available.
2. **Synonym lookup on `harmonized_name`** — if the `harmonized_name` is not a direct schema column but appears in the unified synonym table, the resolved standard key is used. If the resolved key is not in the fixed schema, the normalized key name is stored in `_extra_attributes` (not the raw alias).
3. **Synonym lookup on `attribute_name`** — if `harmonized_name` is absent or unresolvable, the raw `attribute_name` is looked up in the synonym table.
4. **`_extra_attributes`** — any attribute that cannot be resolved by any of the above is serialized as a JSON key-value pair into the `_extra_attributes` column. No data is discarded.

The synonym table (`synonyms.py`) is built from two layers on startup and cached for the lifetime of the process:

- **Layer 1 — `unified.json`** — manually curated synonym lists for all standard keys
- **Layer 2 — `ncbi_attributes.xml`** — the official NCBI BioSample harmonization table; loaded on top of Layer 1 and wins on any conflict

Both `ingestion.py` and `key_mapper.py` import the same `build_synonym_lookup()` function, so the mapping is always identical across both modules.

## Null Normalization

During ingestion, common submitter placeholders for missing values are converted to true missing values (`None` / `NaN`) before any downstream parsing. This applies across structural fields, BioSample attributes, owner/contact provenance, and `lat_lon`.

Examples of values normalized to missing include:

- `missing`, `Missing`, `misssing`
- `N/A`, `na`, `null`, `none`, `-`
- `unknown`, `not provided`, `not collected`, `not applicable`, `not available`, `not determined`
- prefixed forms such as `missing: lab stock` and `missing: data agreement established pre-2023`

This prevents placeholder text from leaking into harmonized columns such as `geo_country`, `host`, `isolation_source`, or `lat_lon`.

## `collected_by` Priority and Submission Provenance

The `collected_by` column is populated with strict priority:

1. **Explicit BioSample attribute** — any `<Attribute harmonized_name="collected_by">` or synonym thereof is the authoritative source and is always preferred.
2. **`<Owner/Name>` fallback** — the submitting institution name from the XML `<Owner>` block is used **only** if no explicit collector attribute was found.

When both are present (e.g. `collected_by = AgBiome` in attributes and `<Owner/Name> = UNC Chapel Hill` in the submission block), the submission-side provenance is preserved losslessly in `_extra_attributes` rather than overwriting the biological collector:

| `_extra_attributes` key | Content |
|---|---|
| `submission_owner` | `<Owner/Name>` value (e.g. `UNC Chapel Hill`) |
| `submission_contact` | Full name from `<Owner/Contacts/Contact>` (e.g. `Rachel Marie Bleich`) |

This ensures that `collected_by` always reflects who physically collected the sample, while institutional and submitter provenance remains accessible without polluting the primary schema columns.

## Geospatial Parsing

`GeoEngine` accepts the standard NCBI `geo_loc_name` field and fills six in-schema columns:
`geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, `geo_sea_ocean`, and `geo_loc_raw`.

Parsing behavior:

| Input | Parsed as |
|---|---|
| `"USA: California, Los Angeles"` | `geo_country=USA`, `geo_region=California`, `geo_locality=Los Angeles`, `geo_iso3166=US` |
| `"USA: California"` | `geo_country=USA`, `geo_region=California`, `geo_iso3166=US` |
| `"Germany, Bavaria"` | `geo_country=Germany`, `geo_locality=Bavaria`, `geo_iso3166=DE` |
| `"France"` | `geo_country=France`, `geo_iso3166=FR` |
| `"Pacific Ocean"` | `geo_sea_ocean=Pacific Ocean`; country/region/locality left empty |
| `"Pacific Ocean: Mariana Trench"` | `geo_sea_ocean=Pacific Ocean`, `geo_locality=Mariana Trench` |
| `"40.71 N, 74.00 W"` or `"40.7128, -74.0060"` | preserved in `geo_loc_raw`; country/region/locality left empty |
| `"missing: lab stock"` or `"not applicable"` | treated as missing; all geo output columns left empty |

Special handling:

- UK sub-country names (`England`, `Scotland`, `Wales`, `Northern Ireland`) are normalized to `geo_country="United Kingdom"` with `geo_iso3166="GB"`.
- Ambiguous `"Korea"` defaults to South Korea (`KR`) with a warning logged.
- Country aliases not handled reliably by `pycountry` are normalized explicitly, including `Turkey -> TR`, `Namibia -> NA`, `Democratic Republic of the Congo -> CD`, and `Burma -> MM`.
- Historical or defunct country names (for example `USSR`, `Yugoslavia`, `Czechoslovakia`) are preserved in `geo_country` and assigned `geo_iso3166="HISTORICAL"` rather than forcing an incorrect modern country code.
- Coordinate-only strings are not reverse-geocoded; they are preserved in `geo_loc_raw` for optional downstream processing.

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

## License

MIT
