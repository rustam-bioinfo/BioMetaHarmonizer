# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Active branch:** `biometaharmonizer_v1`
- **Version:** 0.5.0
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (parsing dates, resolving ISO-3166 geographies, standardizing categorical variables, lossless preservation of all unresolved attributes).

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON/XML data. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion -> Key Harmonization -> Value Parsing -> Categorization -> Output.
- **Fixed Output Schema:** Defined once in `ingestion.py` (`BIOSAMPLE_SCHEMA`, 50 columns). Every record is initialized with all columns; downstream steps fill in-place. No columns are ever added, dropped, or reordered at runtime.
- **Input:** Plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically. Also accepts a Python list directly.
- **Dependencies:** `pandas`, `numpy`, `pycountry`, `python-dateutil`, `biopython`, `requests`, `openpyxl`, `pyarrow`. Note: `sentence-transformers` is NOT a runtime dependency (embeddings layer is optional/offline).
- **Structural Fields (bypass KeyMapper):** Always extracted directly from BioSample XML -- never passed through synonym resolution:
  `biosample_accession`, `biosample_id`, `sra_accession`, `bioproject_accession`,
  `taxonomy_id`, `taxonomy_name`, `organism_name`,
  `submission_date`, `last_update`, `publication_date`, `access`,
  `status`, `status_date`, `title`, `description_comment`, `ncbi_package`, `sample_name_id`.
- **BioProject resolution:** BioProject accession is absent from BioSample XML. Resolved via NCBI assembly summary flat files (RefSeq + GenBank) downloaded once to disk (~100 MB each). `_ensure_assembly_summaries()` is called unconditionally in `ingest()` regardless of input ID type.
- **Working directory:** Assembly summary flat files are cached in `~/.biometaharmonizer/cache/` by default. Override with `set_cache_dir("/content")` in Colab.
- **Lossless attribute preservation:** Any BioSample attribute that does not resolve to a known schema column is stored as JSON in `_extra_attributes`. No data is ever discarded.

### 3. Module Tracking

- [x] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` -- COMPLETE. Current: v0.5.0.
  - Accepts BioSample IDs, GCF_/GCA_ assembly accessions, or mixed lists/files.
  - Resolves BioProject accession from assembly summary flat files.
  - Full XML extraction: Ids, Organism, Description, Package, Status, all Attributes.
  - `organism_name` prefers `<OrganismName>` child element; falls back to `taxonomy_name` XML attribute if absent. Note: `taxonomy_name` is the NCBI name for the assigned taxid and may include strain designators for strain-level taxids (e.g. `Bacillus cereus NC7401`); use `taxonomy_id` for reliable grouping.
  - **`collected_by` priority (v0.5.0):** Explicit BioSample `collected_by` attribute is always preferred. `<Owner/Name>` is applied only as a fallback when no explicit collector attribute was found. If both are present, `<Owner/Name>` is stored as `submission_owner` in `_extra_attributes` and the contact name (`<Owner/Contacts/Contact>` First/Middle/Last) is stored as `submission_contact` in `_extra_attributes`.
  - Retry/backoff for failed batches (up to 3 retries with exponential backoff).
  - Configurable API key via `set_api_key()` or `ingest(api_key=...)` for 10 req/s rate limit.
  - Configurable cache directory via `set_cache_dir()` or `ingest(cache_dir=...)`.
  - Input deduplication via `_deduplicate()`.

- [x] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` -- COMPLETE.
  - Synonym lookup built by `synonyms.py` (`build_synonym_lookup()`): two layers merged at startup and cached per process.
    - Layer 1: `schemas/unified.json` -- manually curated synonym lists (still active and loaded).
    - Layer 2: `schemas/ncbi_attributes.xml` -- NCBI official harmonization table; wins on conflict.
  - Optional semantic fallback via precomputed embeddings (`schemas/ncbi_embeddings.npy`). `SEMANTIC_THRESHOLD = 0.75`. Model loaded lazily. Model name read from `ncbi_cache_meta.json`.
  - Features: `drop_sparse` (int absolute count or float 0-1 fractional fill rate), `drop_junk`, `_PROTECTED_COLUMNS`, `_coalesce_duplicates`.
  - `_warn_missing_mandatory()` returns a compliance DataFrame: `package, field, total_records, filled_records, fill_pct, status`. Thresholds: PASS >= 95%, WARN 80-95%, FAIL < 80%.

- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` -- COMPLETE.
  - Outputs ISO 8601 truncated dates: year-only -> `YYYY`, year-month -> `YYYY-MM`, full -> `YYYY-MM-DD`. No XX placeholders.
  - INSDC date range parsing: `2019/2020` or `2019-01/2020-03` -> start date in `collection_date`, full range in `collection_date_range`.
  - `parse_with_range()` returns a DataFrame with `collection_date` and `collection_date_range` columns.

- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` -- COMPLETE.
  - Output columns: `geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, `geo_sea_ocean`, `geo_loc_raw`.
  - UK sub-country (England/Scotland/Wales/Northern Ireland) -> `United Kingdom` / `GB`.
  - Korea bare name defaults to `KR` with logged warning.
  - Taiwan explicit lookup -> `TW`.
  - Coordinate-only inputs preserved in `geo_loc_raw`.

- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` -- COMPLETE.
  - `TIER1_PATTERNS` is a tuple of `(category, pattern)` pairs; priority order is structurally enforced.
  - Priority order: Environmental > Animal > Human > Food > Lab.
  - `classify_joint(isolation_source_series, host_series)`: classifies isolation_source first, falls back to host.
  - `classify_with_confidence(series)`: returns DataFrame with `one_health_category`, `one_health_term`, `one_health_confidence`.

- [x] **Module 6 (Output):** `src/biometaharmonizer/output.py` -- COMPLETE.
  - `write(df, path, fmt="csv")` supports `csv`, `tsv`, `excel`, `parquet`. Case-insensitive.
  - `write_summary(df, path)` writes `column_name, non_null_count, fill_pct` CSV.

- [x] **Module 7 (CLI):** `src/biometaharmonizer/cli.py` -- COMPLETE.
  - Entrypoint: `biometaharmonizer run --input ids.txt --email user@email.com --output out.csv`
  - Flags: `--api-key`, `--cache-dir`, `--model`, `--threshold`, `--drop-sparse`, `--no-drop-junk`, `--format`, `--summary`, `--skip-dates`, `--skip-geo`, `--skip-one-health`, `--verbose`.

### 4. Schema Architecture (current)
- **`schemas/unified.json`** -- Layer 1 synonym table. Actively loaded by `synonyms.py`.
- **`schemas/ncbi_attributes.xml`** -- NCBI official attribute harmonization table. Layer 2; wins on conflict with unified.json.
- **`schemas/ncbi_embeddings.npy`** -- Precomputed embeddings for semantic fallback. Shape [N, dim], float32.
- **`schemas/ncbi_harmonized_names.json`** -- Sorted list of N harmonized names; row index matches `ncbi_embeddings.npy`.
- **`schemas/ncbi_cache_meta.json`** -- Build metadata: model name, embedding dim, count, timestamp.
- **`schemas/mandatory_fields.json`** -- Maps all 22 `ncbi_package` values to required fields.

### 5. `_extra_attributes` Reserved Keys
In addition to unresolved submitter attributes, the following keys may be written by the ingestion module itself:

| Key | Written when |
|---|---|
| `submission_owner` | `<Owner/Name>` present AND explicit `collected_by` attribute already found |
| `submission_contact` | `<Owner/Contacts/Contact>` First/Middle/Last present |

### 6. Test Infrastructure
- Run all tests: `pytest tests/ -v --tb=short`
- Run with coverage: `pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- Run without network tests: `pytest tests/ -v -m "not network"`
- Current: **170/170 passing**. All tests are self-contained (no live NCBI calls unless `network` marker).
- Known gap: no regression tests yet for `collected_by` priority fix or `submission_owner`/`submission_contact` extraction (added in v0.5.0).

### 7. Colab Setup (every session)
```python
import subprocess, sys, os

REPO_DIR = "/content/BioMetaHarmonizer"
SRC_DIR  = f"{REPO_DIR}/src"

subprocess.run(["git", "-C", REPO_DIR, "pull"], check=True)

for key in list(sys.modules.keys()):
    if "biometaharmonizer" in key:
        del sys.modules[key]

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

os.chdir("/content")

import biometaharmonizer
print(biometaharmonizer.__version__)  # should print 0.5.0
```

### 8. Next Steps (in priority order)

1. **Regression tests for v0.5.0 ingestion fixes**
   - Test that explicit `collected_by` attribute wins over `<Owner/Name>`.
   - Test that `submission_owner` and `submission_contact` appear in `_extra_attributes` when both sources are present.
   - Test fallback: when no `collected_by` attribute exists, `<Owner/Name>` populates `collected_by` cleanly.

2. **Validate `_extra_attributes` JSON in downstream workflows**
   - Utility function or notebook snippet to flatten/expand `_extra_attributes` into separate columns for exploratory analysis.
   - Document common keys found in large-scale runs (e.g. `antimicrobial_resistance`, `temp`, `ph`, `depth`).

3. **Benchmark key harmonization fill rates**
   - Run on a representative dataset (e.g. 6,000+ *Bacillus cereus* group genomes).
   - Compare fill rates per column: `unified.json` baseline vs. `ncbi_attributes.xml` + embeddings.
   - Results feed directly into manuscript Methods section.

4. **Review `organism_name` vs `taxonomy_name` redundancy**
   - Quantify how often the two columns differ across a large run.
   - Consider whether `organism_name` should move to `_extra_attributes` by default or remain a primary column.

5. **One Health classifier expansion**
   - Add Tier 2 patterns for finer-grained categories (e.g. Livestock, Wildlife, Aquatic within Animal).
   - Evaluate recall on large *Bacillus cereus* and *Klebsiella pneumoniae* datasets.

6. **PyPI release**
   - Finalize `MANIFEST.in`.
   - Test twine upload to TestPyPI.
   - Publish to PyPI as `biometaharmonizer==0.5.0`.

7. **Manuscript**
   - Methods section: describe fixed schema, two-layer synonym resolution, `collected_by` priority logic, lossless `_extra_attributes` design.
   - Benchmarking figures: fill rate heatmaps per column and per package, before/after harmonization.

### 9. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence and state what the next immediate task is.
