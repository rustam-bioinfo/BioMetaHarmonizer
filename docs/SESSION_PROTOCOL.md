# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Version:** 0.2.0 (next: 0.3.0 after KeyMapper rewrite)
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (parsing dates, resolving ISO-3166 geographies, standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of 6,618 *B. cereus* group genomes. Ingestion yields 6,508 records (6,618 - 110 fetch failures). After KeyMapper: 6,508 × 195 columns.

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON/XML data. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion → Key Harmonization → Value Parsing → Categorization → Output.
- **Input:** Two accepted formats — a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically.
- **Dependencies (current):** `pandas`, `numpy`, `rapidFuzz`, `pycountry`, `python-dateutil`, `biopython`, `requests`.
- **Dependencies (after Option C):** replace `rapidFuzz` with `sentence-transformers` (model: `all-MiniLM-L6-v2`).
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
- [ ] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` — NEEDS REWRITE (Option C).
  - Current state: functional and validated on B. cereus dataset (6,508 × 195 output).
  - Current approach: static `unified.json` synonym dict + RapidFuzz fuzzy matching. NOT publication-worthy.
  - **Planned rewrite (Option C — Hybrid):**
    - **Layer 1 (authoritative):** Fetch NCBI BioSample harmonization table from
      `https://www.ncbi.nlm.nih.gov/biosample/docs/attributes/?format=xml` once at install/first run.
      Cache as `schemas/ncbi_attributes.xml`. This table maps every known submitter synonym to
      its NCBI `HarmonizedName`. Replaces `unified.json` entirely for known synonyms.
    - **Layer 2 (semantic fallback):** For column names absent from the NCBI table, use
      `sentence-transformers` (`all-MiniLM-L6-v2`) cosine similarity against embeddings of all
      harmonized names. Threshold ~0.75. Handles typos, novel lab-specific keys, language variants.
    - **Embeddings:** Precomputed at install time for all ~500 NCBI harmonized names.
      Stored as `schemas/ncbi_embeddings.npy` and shipped in the repo for fully offline use.
    - **Publication argument:** "BioMetaHarmonizer resolves attribute synonyms using the NCBI
      BioSample harmonization table as primary authority, supplemented by a semantic embedding
      model for attributes absent from the official table. This approach requires no manual
      curation and is automatically updated with each NCBI attribute release."
  - Current features to preserve: `drop_sparse`, `drop_junk`, `_PROTECTED_COLUMNS`,
    `_coalesce_duplicates`, per-package `_warn_missing_mandatory()` via `mandatory_fields.json`.
- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — COMPLETE. 79.3% fill on B. cereus dataset.
- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — COMPLETE. 94.9% country, 58.8% region.
- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — COMPLETE. 76.6% classified. Tier 1 Regex with word-boundary fixes.

### 4. Schema Architecture (current)
- **`schemas/unified.json`** — 29-field manually curated synonym lookup. Will be REPLACED by NCBI attribute XML in Option C rewrite. Kept until rewrite is complete.
- **`schemas/mandatory_fields.json`** — Maps all 22 `ncbi_package` values (including `Human.1.0`, `Plant.1.0`) to required fields. Includes `default` fallback. RETAINED in Option C — mandatory validation is independent of synonym resolution.
- **`schemas/pathogen_cl_1.0.json`** — Legacy. No longer loaded by KeyMapper.
- **`schemas/pathogen_env_1.0.json`** — Legacy. No longer loaded by KeyMapper.
- **After Option C:** `unified.json` replaced by `ncbi_attributes.xml` + `ncbi_embeddings.npy`.

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
- **Current total: 161/161 tests passing.**
- NOTE: `test_key_mapper.py` does not yet cover `drop_sparse`, `drop_junk`, or per-package mandatory validation. These tests must be written before the Option C rewrite.

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
print(biometaharmonizer.__version__)  # should print 0.2.0
```

### 9. Next Steps (in priority order)
1. **Option C rewrite of `key_mapper.py`** — the most important task.
   - Step 1: Write `scripts/build_ncbi_attribute_cache.py` — fetches NCBI attribute XML,
     parses all harmonized names + synonyms, precomputes `all-MiniLM-L6-v2` embeddings,
     saves `schemas/ncbi_attributes.xml` and `schemas/ncbi_embeddings.npy`.
   - Step 2: Rewrite `KeyMapper.__init__()` to load from `ncbi_attributes.xml` + `ncbi_embeddings.npy`.
   - Step 3: Replace `_build_lookup()` + RapidFuzz with two-layer resolution:
     Layer 1 = exact/synonym match against NCBI table; Layer 2 = embedding cosine similarity fallback.
   - Step 4: Remove `rapidFuzz` dependency; add `sentence-transformers` and `numpy` to `requirements.txt`.
   - Step 5: Update `unified.json` status to legacy in schemas directory.
   - Step 6: Bump version to 0.3.0.
2. **Update `tests/test_key_mapper.py`** — add tests for `drop_sparse`, `drop_junk`,
   `_PROTECTED_COLUMNS` protection, and per-package `_warn_missing_mandatory()`.
3. **`tests/test_pipeline.py`** — end-to-end integration test.
4. **Output module** — `src/biometaharmonizer/output.py`.
5. **CLI entrypoint** — `biometaharmonizer run --input ids.txt --email user@email.com --output harmonized.csv`.
6. **PyPI packaging** — finalize `pyproject.toml`, `MANIFEST.in`.
7. **Manuscript benchmarking** — measure parse rates per field before/after Option C for Application Note results section. Before = current static dict, After = NCBI table + embeddings. The delta demonstrates the value of the approach.

### 10. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence and state what the next immediate task is. Do not ask what to work on — begin Option C Step 1 immediately.
