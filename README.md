# BioMetaHarmonizer

A universal Python package for harmonizing, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

## Overview

The NCBI BioSample database is the central repository for genomic metadata. However, because submissions are predominantly free-text and crowd-sourced, the metadata is highly unstructured, error-prone, and chaotic. **BioMetaHarmonizer** provides a lightweight, pip-installable solution that maps and harmonizes the entire BioSample attribute matrix into a clean, machine-readable format.

## Architecture

The tool uses a **config-driven design**: one set of core parsing modules driven by external JSON schema files representing official NCBI submission templates.

```
BioMetaHarmonizer/
├── src/
│   └── biometaharmonizer/
│       ├── __init__.py
│       ├── ingestion.py       # Module 1: Data Ingestion
│       ├── key_mapper.py      # Module 2: Key Harmonization
│       ├── date_engine.py     # Module 3: Temporal Parsing
│       ├── geo_engine.py      # Module 4: Geospatial Resolution
│       └── one_health.py      # Module 5: Biological Categorization
├── schemas/
│   ├── pathogen_cl_1.0.json
│   └── pathogen_env_1.0.json
├── tests/
│   └── test_date_engine.py
├── docs/
│   └── SESSION_PROTOCOL.md
├── setup.py
├── requirements.txt
└── README.md
```

## Modules

| Module | File | Status |
|---|---|---|
| 1. Ingestion | `ingestion.py` | Pending |
| 2. Key Harmonization | `key_mapper.py` | Pending |
| 3. Temporal Parsing | `date_engine.py` | Pending |
| 4. Geospatial Resolution | `geo_engine.py` | Pending |
| 5. One Health Categorization | `one_health.py` | Pending |

## Target Publication

*Bioinformatics* (Application Note) or *Nucleic Acids Research*

## License

MIT
