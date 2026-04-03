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
- **Structural Fields (bypass KeyMapper):** The following columns are always extracted directly from the BioSample XML structure and are NOT passed through synonym resolution:
  `biosample_accession`, `biosample_id`, `sra_accession`, `bioproject_accession`,
  `taxonomy_id`, `taxonomy_name`, `organism_name`,
  `submission_date`, `last_update`, `publication_date`, `access`,
  `status`, `status_date`, `title`, `description_comment`, `ncbi_package`, `sample_name_id`.

### 3. Module Tracking
- [x] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` — COMPLETE. 22/22 unit tests passing. Full XML extraction: all BioSample blocks now parsed (Ids, Organism, Description, Package, Status, structural dates).
- [x] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` — COMPLETE. 24/24 unit tests passing.
- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` — COMPLETE. 22/22 unit tests passing.
- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` — COMPLETE. 18/18 unit tests passing.
- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` — COMPLETE. 26/26 unit tests passing. Tier 1 Regex with word-boundary fixes. Validated on real B. cereus NCBI data.

### 4. Schemas
- `schemas/pathogen_cl_1.0.json` — Complete. Expanded to 20 fields. Covers: collection_date, geo_loc_name, lat_lon, host, isolation_source, host_disease, isolate, sub_strain, serotype, serovar, host_age, host_sex, host_tissue_sampled, genotype, antimicrobial_resistance, outbreak, collected_by, sequencing_method, assembly_method, culture_collection.
- `schemas/pathogen_env_1.0.json` — Complete. Expanded to 16 fields. Covers: collection_date, geo_loc_name, lat_lon, isolation_source, env_broad_scale, env_local_scale, env_medium, isolate, samp_size, samp_mat_process, water_env_biome, temp, ph, collected_by, sequencing_method, assembly_method.

### 5. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- Run all tests: `!pytest tests/ -v --tb=short`
- Run with coverage: `!pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- **Current total: 114/114 tests passing.**
- NOTE: Unit tests for `ingestion.py` test the old `_parse_biosample_xml` signature. They need to be updated to assert the new structural fields.

### 6. Next Steps (in priority order)
1. **Update ingestion unit tests** — `tests/test_ingestion.py`: add assertions for all new structural fields (`sra_accession`, `bioproject_accession`, `taxonomy_id`, `taxonomy_name`, `organism_name`, `submission_date`, `last_update`, `publication_date`, `access`, `status`, `status_date`, `title`, `description_comment`, `ncbi_package`).
2. **End-to-end integration test** — `tests/test_pipeline.py`: run the full Ingestion -> KeyMapper -> DateEngine + GeoEngine + OneHealth pipeline on a real batch of ~50 B. cereus BioSamples and assert output shape and column completeness.
3. **Output module** — `src/biometaharmonizer/output.py`: write harmonized DataFrame to CSV and optionally to an Excel file with per-column parse reports.
4. **CLI entrypoint** — `pyproject.toml` script: `biometaharmonizer run --input ids.txt --schema pathogen_cl --email user@email.com --output harmonized.csv`.
5. **PyPI packaging** — finalize `pyproject.toml`, `MANIFEST.in`, `README.md` with usage examples for the manuscript.
6. **Manuscript benchmarking** — run on full 6,618 B. cereus dataset, measure parse rates per field for the Application Note results section.

### 7. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence. Finally, ask me which module or file we are working on today.
