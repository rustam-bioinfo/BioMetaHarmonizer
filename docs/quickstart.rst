.. _quickstart:

==========
Quickstart
==========

All three examples below use the actual public API of BioMetaHarmonizer.
Every parameter name matches the signature of :func:`biometaharmonizer.ingestion.ingest`
exactly as it appears in the source code.

Before Your First Run
---------------------

BioMetaHarmonizer's One Health classification relies on two schema files that
must be built once after installation:

.. code-block:: bash

   # Step 1 — download and cache NCBI BioSample attribute definitions
   biometaharmonizer build-ncbi-cache

   # Step 2 — build the enriched One Health term dictionary
   biometaharmonizer build-dicts

Both commands write their output to ``src/biometaharmonizer/schemas/`` and are
safe to re-run at any time to pick up upstream changes from NCBI or OLS.
See :ref:`cli_reference` for the full list of options for each command.

Example 1 — Minimal: Three BioSample Accessions
-------------------------------------------------

Fetch three NCBI BioSample records and save the result to a CSV file.

.. code-block:: python

   import biometaharmonizer as bmh

   df = bmh.ingest(
       source=["SAMN02436525", "SAMN02434874", "SAMN02429261"],
       email="your@email.com",
   )

   bmh.write(df, "output.csv", fmt="csv")
   print(df.shape)         # (3, 57)
   print(df.columns.tolist())

The returned DataFrame always has exactly **57 columns** in the order listed
in :ref:`schema_reference`. Every column is pre-initialised to ``None``/NaN
for records that do not carry that attribute.

Example 2 — Large-Scale Run with API Key and Custom Cache
----------------------------------------------------------

Read accessions from a plain-text file (one per line), enable API key
authentication, override the cache directory, and tune batch sizes.

.. code-block:: python

   import biometaharmonizer as bmh

   df = bmh.ingest(
       source="accessions.txt",          # Path or str to a file; one accession per line
       email="your@email.com",
       api_key="YOUR_NCBI_API_KEY",
       cache_dir="/data/bmh_cache",      # Custom cache for assembly summary files
       fetch_batch_size=500,             # Records per efetch request (default: 200)
       esearch_batch_size=200,           # Overrides the module default of 100; CLI default is 200
       refresh_cache=False,              # Set True to force re-download of assembly index
   )

   bmh.write(df, "harmonized.parquet", fmt="parquet")
   bmh.write_summary(df, "fill_rates.csv")

``accessions.txt`` may contain BioSample accessions (``SAMN``/``SAME``/``SAMD``),
assembly accessions (``GCF_``/``GCA_``), or a mix of both — the tool classifies
them automatically via the internal ``_classify_ids()`` function.

Example 3 — Assembly Accession Input
--------------------------------------

Use GCF\_/GCA\_ accessions directly. The tool resolves them to BioSample
accessions in a two-step process:

1. **Local index lookup:** both ``assembly_summary_refseq.txt`` and
   ``assembly_summary_genbank.txt`` are searched for matching
   ``assembly_accession`` values. This step is instant for cached files.
2. **Entrez elink fallback:** any accessions not found in the local index are
   resolved via a live ``Entrez.esearch`` + ``Entrez.esummary`` call against
   the ``biosample`` database.

.. code-block:: python

   import biometaharmonizer as bmh

   # Mixed list: RefSeq and GenBank assembly accessions
   accessions = [
       "GCF_000009045.1",   # Bacillus subtilis 168
       "GCA_000005845.2",   # E. coli K-12 MG1655
       "GCF_000210835.1",   # Klebsiella pneumoniae NTUH-K2044
   ]

   df = bmh.ingest(
       source=accessions,
       email="your@email.com",
       refresh_cache=False,
   )

   # The resolved assembly accessions are back-filled from the index:
   print(df[["biosample_accession",
             "assembly_accession_refseq",
             "assembly_accession_genbank"]])

Output Columns
--------------

The fixed output schema (returned by every ``ingest()`` call) contains
exactly **57 columns** in the following order (matching ``_load_final_schema()``):

.. code-block:: python

   [
       # Block 1 — Record identity
       "biosample_accession", "biosample_id", "sra_accession",
       "bioproject_accession", "assembly_accession_refseq",
       "assembly_accession_genbank", "sample_name_id",
       # Block 2 — Taxonomy
       "taxonomy_id", "taxonomy_name", "organism_name",
       # Block 3 — Specimen identity
       "isolate", "strain", "sub_strain",
       "serotype", "serovar", "genotype", "culture_collection",
       # Block 4 — Host
       "host", "host_disease", "host_age", "host_sex",
       "host_tissue_sampled",
       # Block 5 — Isolation source + environmental context
       "isolation_source", "sample_type",
       "env_broad_scale", "env_local_scale", "env_medium",
       # Block 6 — Geography
       "geo_loc_name", "lat_lon",
       "geo_country", "geo_region", "geo_locality",
       "geo_iso3166", "geo_sea_ocean",
       # Block 7 — Temporal
       "collection_date", "collection_date_range",
       # Block 8 — One Health (derived)
       "one_health_category", "one_health_confidence",
       "one_health_evidence_level",
       # Block 9 — Epidemiology
       "outbreak",
       # Block 10 — Sequencing / assembly
       "sequencing_method", "assembly_method",
       # Block 11 — Collection provenance
       "collected_by",
       # Block 12 — NCBI administrative
       "ncbi_package", "submission_date", "last_update",
       "publication_date", "access", "status", "status_date",
       "title", "description_comment",
       # Overflow
       "_extra_attributes",
   ]

See :ref:`schema_reference` for the full description of every column.
