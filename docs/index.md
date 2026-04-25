# BioMetaHarmonizer — Documentation

**Last updated:** 2026-04-25

BioMetaHarmonizer is a Python package for harmonizing NCBI BioSample and BioProject metadata into a standardised, analysis-ready format.
It normalises free-text fields (collection date, geographic location, host, isolation source) and classifies samples into One Health categories (Human, Animal, Plant, Food, Environmental, Lab).

## Documents

| File | Contents |
|---|---|
| [architecture.md](architecture.md) | Package structure, module responsibilities, data flow |
| [build_dictionaries.md](build_dictionaries.md) | How `one_health_dictionaries.json` is built and maintained |

## Quick start

```bash
pip install -e .

# harmonise a BioProject
biometaharmonizer --bioproject PRJNA12345 --output results/

# rebuild the dictionaries (downloads ~65 MB taxonomy dump once)
python scripts/build_dictionaries.py \
    --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
    --output src/biometaharmonizer/schemas/one_health_dictionaries.json
```

## Repository layout

```
BioMetaHarmonizer/
├── docs/                          # This documentation
├── scripts/
│   └── build_dictionaries.py      # Dictionary rebuild script
├── src/biometaharmonizer/
│   ├── cli.py                     # Entry point (argparse)
│   ├── ingestion.py               # NCBI API fetch + record normalisation
│   ├── one_health.py              # One Health category classifier
│   ├── date_engine.py             # Collection date normalisation
│   ├── geo_engine.py              # Geographic location normalisation
│   ├── key_mapper.py              # Field name canonicalisation
│   ├── synonyms.py                # Synonym expansion helpers
│   ├── output.py                  # TSV / JSON output writers
│   └── schemas/
│       └── one_health_dictionaries.json   # Compiled classification dictionary
├── pyproject.toml
└── requirements.txt
```
