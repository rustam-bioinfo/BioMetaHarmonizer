# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (specifically parsing dates, resolving ISO-3166 geographies, and standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of ~6,618 *B. cereus* group genomes (5,145 GCF_ / 1,473 GCA_).

### 2. Core Architecture Rules
- **Config-Driven Design:** The tool uses one set of core parsing modules (Date, Geo, Mapper) driven by external schema files (JSON) representing official NCBI templates. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion -> Key Harmonization -> Value Parsing -> Categorization -> Output.
- **Input:** Two accepted formats — a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or a plain `.txt` list of assembly accessions (GCF_/GCA_). Mixed files handled automatically.
- **Dependencies:** `pandas`, `numpy`, `rapidFuzz`, `pycountry`, `python-dateutil`, `biopython`, `requests`.

### 3. Module Tracking
- [x] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` — COMPLETE. 22/22 unit tests passing. Supports GCF_/GCA_ and SAMN_ inputs, assembly_summary flat-file resolution, Entrez batch fetcher, BioSample XML parser.
- [ ] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` — Skeleton complete. Needs synonym expansion and unit tests.
- [ ] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — Skeleton complete. 5/5 basic tests passing. Needs edge-case expansion on real B. cereus data.
- [ ] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — Skeleton complete. Needs unit tests and stress testing.
- [ ] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — Skeleton complete. Tier 1 Regex done. NLP Tier 2 pending.

### 4. Schemas
- `schemas/pathogen_cl_1.0.json` — Complete.
- `schemas/pathogen_env_1.0.json` — Complete.

### 5. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- Run tests in Colab: `!pytest tests/ -v --tb=short`
- Current total: **27/27 tests passing**.

### 6. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence. Finally, ask me which module or file we are working on today.
