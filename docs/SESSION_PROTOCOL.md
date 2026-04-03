# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Version:** 0.2.0
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (specifically parsing dates, resolving ISO-3166 geographies, and standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of 6,618 *B. cereus* group genomes. Ingestion yields 6,508 records (6,618 - 110 fetch failures). After KeyMapper: 6,508 × 195 columns.

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON schema files. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion → Key Harmonization → Value Parsing → Categorization → Output.
- **Input:** Two accepted formats — a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically.
- **Dependencies:** `pandas`, `numpy`, `rapidFuzz`, `pycountry`, `python-dateutil`, `biopython`, `requests`.
- **Structural Fields (bypass KeyMapper):** Always extracted directly from BioSample XML — never passed through synonym resolution:
  `biosample_accession`, `biosample_id`, `sra_accession`, `bioproject_accession`,
  `taxonomy_id`, `taxonomy_name`, `organism_name`,
  `submission_date`, `last_update`, `publication_date`, `access`,
  `status`, `status_date`, `title`, `description_comment`, `ncbi_package`, `sample_name_id`.
- **BioProject resolution:** BioProject accession is absent from BioSample XML. Resolved via NCBI assembly summary flat files (RefSeq + GenBank) downloaded once to disk (~100 MB each). `_ensure_assembly_summaries()` is called unconditionally in `ingest()` regardless of input ID type.
- **Working directory:** `ingest()` must be called with `os.chdir("/content")` set in Colab so assembly summary files download to `/content/` and are found by `Path("assembly_summary_refseq.txt")`.

### 3. Module Tracking
- [x] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` — COMPLETE.
  - Accepts BioSample IDs, GCF_/GCA_ assembly accessions, or mixed lists/files.
  - Resolves BioProject accession from assembly summary flat files (96.4% fill on B. cereus dataset).
  - Full XML extraction: Ids, Organism, Description, Package, Status, all Attributes.
  - `organism_name` = `taxonomy_name` attribute of `<Organism>` element (no `<OrganismName>` child exists in real NCBI XML).
- [x] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` — COMPLETE.
  - Loads `schemas/unified.json` by default (no path argument needed).
  - Synonym lookup + fuzzy matching (RapidFuzz, threshold 85).
  - Coalesces duplicate columns (first-non-null wins).
  - `drop_junk=True`: drops person-name columns (regex: Title Case word pairs).
  - `drop_sparse=5`: drops columns with <5 non-null values. Both respect `_PROTECTED_COLUMNS`.
  - `_warn_missing_mandatory()`: groups by `ncbi_package`, checks fill rate per package against `mandatory_fields.json`, warns only when fill < 50%.
- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — COMPLETE. 79.3% fill on B. cereus dataset.
- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — COMPLETE. 94.9% country, 58.8% region.
- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — COMPLETE. 76.6% classified. Tier 1 Regex with word-boundary fixes.

### 4. Schema Architecture
- **`schemas/unified.json`** — 29-field unified synonym lookup covering all NCBI packages. `mandatory` flag removed from fields — mandatory validation is per-package only.
- **`schemas/mandatory_fields.json`** — Maps all 20 `ncbi_package` values found in the B. cereus dataset to their required fields. Includes `default` fallback.
- **`schemas/pathogen_cl_1.0.json`** — Legacy. No longer loaded by KeyMapper. Retained for reference.
- **`schemas/pathogen_env_1.0.json`** — Legacy. No longer loaded by KeyMapper. Retained for reference.
- **To extend:** Add synonyms to `unified.json` or add a package entry to `mandatory_fields.json`. No Python changes needed.

### 5. Validated Performance (B. cereus, n=6,618)

| Field | Fill rate |
|---|---|
| `biosample_accession` | 100% |
| `organism_name` | 95.8% |
| `bioproject_accession` | 96.4% |
| `collection_date` (parsed) | 79.3% |
| `geo_loc_name` country | 94.9% |
| `geo_loc_name` region | 58.8% |
| `one_health_category` | 76.6% |

Raw shape: 6,508 × 346. Post-KeyMapper: 6,508 × 195.

### 6. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- Run all tests: `!pytest tests/ -v --tb=short`
- Run with coverage: `!pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- **Current total: 161/161 tests passing.**

### 7. Next Steps (in priority order)
1. **Update `tests/test_key_mapper.py`** — add tests for `drop_sparse`, `drop_junk`, `_drop_junk_columns`, `_drop_sparse_columns`, `_PROTECTED_COLUMNS` protection, and per-package `_warn_missing_mandatory()`.
2. **`tests/test_pipeline.py`** — end-to-end integration test: Ingestion → KeyMapper → DateEngine + GeoEngine + OneHealth on a mocked batch of ~50 B. cereus BioSamples. Assert output shape and column completeness.
3. **Output module** — `src/biometaharmonizer/output.py`: write harmonized DataFrame to CSV and optionally Excel with per-column parse reports.
4. **CLI entrypoint** — `pyproject.toml` script: `biometaharmonizer run --input ids.txt --email user@email.com --output harmonized.csv`.
5. **PyPI packaging** — finalize `pyproject.toml`, `MANIFEST.in`.
6. **Manuscript benchmarking** — run on full 6,618 B. cereus dataset; measure parse rates per field for Application Note results section.

### 8. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence. Finally, ask me which module or file we are working on today.
