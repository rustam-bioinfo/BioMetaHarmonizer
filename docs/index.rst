.. _index:

=====================================
BioMetaHarmonizer Documentation
=====================================

BioMetaHarmonizer is a universal Python library and command-line tool for fetching,
harmonizing, and standardizing NCBI BioSample metadata at scale. It resolves both
BioSample accessions (SAMN/SAME/SAMD) and assembly accessions (GCF\_/GCA\_) to
a fixed 57-column output schema, parses collection dates to ISO 8601 format,
resolves geographic locations to ISO 3166-1 country codes, classifies isolation
sources into One Health categories (Human, Animal, Food, Environmental, etc.),
and extracts antibiogram drug-susceptibility tables from NCBI Pathogen records.
The tool is aimed at computational biologists and epidemiologists working with
large-scale genomic metadata from public repositories.

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   user_guide/ingestion
   user_guide/harmonization
   user_guide/antibiogram
   user_guide/output

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api_reference
   cli_reference
   schema_reference

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   developer_guide/scripts
   faq
   changelog
