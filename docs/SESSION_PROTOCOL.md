# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Version:** 0.3.0
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (parsing dates, resolving ISO-3166 geographies, standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of 6,618 *B. cereus* group genomes. Ingestion yields 6,508 records (6,618 - 110 fetch failures). After KeyMapper: 6,508 × 195 columns.

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON/XML data. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion → Key Harmonization → Value Parsing → Categorization → Output.
- **Input:** Two accepted formats — a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically.
- **Dependencies:** `pandas`, `numpy`, `sentence-transformers`, `pycountry`, `python-dateutil`, `biopython`, `requests`, `openpyxl`, `pyarrow`.
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
  - Option C: NCBI attribute XML (Layer 1) + sentence-transformers all-MiniLM-L6-v2 (Layer 2). rapidfuzz removed.
  - Layer 1: exact/synonym lookup from `schemas/ncbi_attributes.xml` (built by `scripts/build_ncbi_attribute_cache.py`).
  - Layer 2: cosine similarity fallback via precomputed embeddings (`schemas/ncbi_embeddings.npy`). `SEMANTIC_THRESHOLD = 0.75`. Model loaded lazily on first use.
  - Features preserved: `drop_sparse`, `drop_junk`, `_PROTECTED_COLUMNS`, `_coalesce_duplicates`, per-package `_warn_missing_mandatory()` via `mandatory_fields.json`.
  - Publication argument: "BioMetaHarmonizer resolves attribute synonyms using the NCBI BioSample harmonization table as primary authority, supplemented by a semantic embedding model for attributes absent from the official table. This approach requires no manual curation and is automatically updated with each NCBI attribute release."
- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — COMPLETE. 79.3% fill on B. cereus dataset.
- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — COMPLETE. 94.9% country, 58.8% region.
- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — COMPLETE. 76.6% classified. Tier 1 Regex with word-boundary fixes.
- [x] **Module 6 (Output):** `src/biometaharmonizer/output.py` — COMPLETE. `write()` (csv/tsv/excel/parquet) and `write_summary()` implemented.

### 4. Schema Architecture (current)
- **`schemas/unified.json`** — LEGACY (superseded by `ncbi_attributes.xml` in v0.3.0). Not loaded by KeyMapper.
- **`schemas/mandatory_fields.json`** — Maps all 22 `ncbi_package` values (including `Human.1.0`, `Plant.1.0`) to required fields. Includes `default` fallback. RETAINED — mandatory validation is independent of synonym resolution.
- **`schemas/pathogen_cl_1.0.json`** — Legacy. Not loaded by KeyMapper.
- **`schemas/pathogen_env_1.0.json`** — Legacy. Not loaded by KeyMapper.
- **`schemas/ncbi_attributes.xml`** — NCBI official attribute harmonization table, fetched by `scripts/build_ncbi_attribute_cache.py`.
- **`schemas/ncbi_embeddings.npy`** — Precomputed all-MiniLM-L6-v2 embeddings, shape [N, 384], float32.
- **`schemas/ncbi_harmonized_names.json`** — Sorted list of N harmonized names; row index matches `ncbi_embeddings.npy`.

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

### 6. KeyMapper Warning Behavior
- Packages with fewer than `MIN_WARN_GROUP_SIZE = 10` records are silently skipped.
- Warnings fire only when mandatory field fill rate < 50% for that package's records.
- Known data quality issues in NCBI (not bugs): `Pathogen.env.1.0` env fields near 0%,
  all `MIGS.ba.*` `isolation_source` near 0%, `Generic.1.0` core fields ~17-19%.
  These reflect widespread NCBI submission non-compliance, not tool failures.

### 7. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- Run all tests: `!pytest tests/ -v --tb=short`
- Run with coverage: `!pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- **Current total: 172/172 tests passing.**
- `test_key_mapper.py`: covers `drop_sparse`, `drop_junk`, `_PROTECTED_COLUMNS` protection, per-package mandatory validation, Option C exact-synonym and semantic resolution, and RuntimeError on missing cache.
- `test_output.py`: covers write() (csv/tsv/parquet/excel/invalid), parent dir creation, return type, write_summary() fill rates.
- `test_pipeline.py`: end-to-end integration — KeyMapper rename, DateEngine, GeoEngine, OneHealthClassifier, output write.

### 8. Colab Setup (every session)
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

os.chdir("/content")  # required for assembly summary flat file paths

import biometaharmonizer
print(biometaharmonizer.__version__)  # should print 0.3.0
```

### 9. Next Steps (in priority order)
1. **CLI entrypoint** — `src/biometaharmonizer/cli.py`
   `biometaharmonizer run --input ids.txt --email user@email.com --output out.csv`
2. **Run `build_ncbi_attribute_cache.py` on real NCBI data** and validate fill rate improvement vs. `unified.json` baseline.
3. **Manuscript benchmarking** — parse rates per field before/after Option C (baseline = unified.json, test = ncbi_attributes.xml + embeddings). The delta demonstrates the value of the approach.
4. **PyPI release** — finalize `MANIFEST.in`, test twine upload to TestPyPI.
5. **Application Note draft** — target *Bioinformatics* / *GigaScience* / *Microbial Genomics*.

### 10. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence and state what the next immediate task is. Do not ask what to work on — begin Option C Step 1 immediately.
