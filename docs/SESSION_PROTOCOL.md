# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (specifically parsing dates, resolving ISO-3166 geographies, and standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of several thousand *B. cereus* group genomes.

### 2. Core Architecture Rules
- **Config-Driven Design:** The tool uses one set of core parsing modules (Date, Geo, Mapper) driven by external schema files (JSON) representing official NCBI templates. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion -> Key Harmonization -> Value Parsing -> Categorization -> Output.
- **Dependencies:** `pandas`, `numpy`, `rapidFuzz`, `pycountry`, `python-dateutil`, `biopython`, `requests`.

### 3. Module Tracking
- [ ] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` — Skeleton complete. NCBI API fetcher pending.
- [ ] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` — Skeleton complete. Needs synonym expansion.
- [ ] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — Skeleton complete. Needs edge-case testing on B. cereus data.
- [ ] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — Skeleton complete. Needs stress testing.
- [ ] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — Skeleton complete. NLP Tier 2 pending.

### 4. Schemas
- `schemas/pathogen_cl_1.0.json` — Complete.
- `schemas/pathogen_env_1.0.json` — Complete.

### 5. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence. Finally, ask me which module or file we are working on today.
