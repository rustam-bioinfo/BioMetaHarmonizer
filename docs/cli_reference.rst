.. _cli_reference:

=============
CLI Reference
=============

BioMetaHarmonizer installs a ``biometaharmonizer`` entry point that is
registered in ``pyproject.toml`` as::

    biometaharmonizer = "biometaharmonizer.cli:main"

The CLI exposes four subcommands. Run ``build-ncbi-cache`` and ``build-dicts``
once after installation to prepare the schema files required for One Health
classification, then use ``run`` for harmonization and ``generate-report`` to
produce HTML/PDF summary reports.

.. code-block:: bash

   # One-time setup (run after installation)
   biometaharmonizer build-ncbi-cache
   biometaharmonizer build-dicts

   # Regular use
   biometaharmonizer run --help
   biometaharmonizer generate-report --help

   # Version
   biometaharmonizer --version

----

build-ncbi-cache
----------------

Downloads the NCBI BioSample attribute definitions XML and saves it to the
``schemas/`` directory. This file is consumed by ``build-dicts`` to resolve
NCBI-specific attribute names during dictionary enrichment.

**Run this command once after installation, and again whenever NCBI updates
their BioSample attribute definitions.**

**Usage:**

.. code-block:: bash

   biometaharmonizer build-ncbi-cache \
       [--output-dir <DIR>] \
       [--skip-fetch]

**Flags:**

.. list-table::
   :header-rows: 1

   * - Flag
     - Type
     - Default
     - Description
   * - ``--output-dir``
     - str
     - ``src/biometaharmonizer/schemas/``
     - Directory where the downloaded attribute XML is saved.
   * - ``--skip-fetch``
     - flag
     - False
     - Skip the network download and process only files already present in
       ``--output-dir``. Useful for air-gapped environments.

**Example:**

.. code-block:: bash

   # Standard first-time setup
   biometaharmonizer build-ncbi-cache

   # Use a custom output directory
   biometaharmonizer build-ncbi-cache --output-dir /data/bmh_schemas

   # Re-process already-downloaded files without hitting the network
   biometaharmonizer build-ncbi-cache --skip-fetch

----

build-dicts
-----------

Builds the enriched ``one_health_dictionaries.json`` from OLS4 ontology data,
NCBI Taxonomy, and UMLS. This file drives the One Health classification step
in ``biometaharmonizer run``.

**Run this command once after installation (after ``build-ncbi-cache``), and
again whenever you want to refresh classification terms from upstream
ontology sources.**

**Usage:**

.. code-block:: bash

   biometaharmonizer build-dicts \
       [--base   <FILE>] \
       [--output <FILE>] \
       [--taxdmp <PATH>] \
       [--umls-key <KEY>] \
       [--skip-ols] \
       [--skip-ncbi] \
       [--dry-run] \
       [--verbose-collisions]

**Flags:**

.. list-table::
   :header-rows: 1

   * - Flag
     - Type
     - Default
     - Description
   * - ``--base``
     - str
     - ``src/biometaharmonizer/schemas/one_health_dictionaries.json``
     - Path to the hand-curated base dictionary. Entries in this file are
       never overwritten (``base_wins`` strategy).
   * - ``--output``
     - str
     - ``src/biometaharmonizer/schemas/one_health_dictionaries.json``
     - Destination path for the enriched dictionary. Defaults to overwriting
       the bundled file in place.
   * - ``--taxdmp``
     - str
     - None
     - Path to a local ``taxdmp.zip`` or an already-extracted directory
       containing ``names.dmp`` and ``nodes.dmp``. When omitted, the ~65 MB
       archive is downloaded automatically from NCBI FTP.
   * - ``--umls-key``
     - str
     - None
     - UMLS API key for enriching terms from the UMLS Metathesaurus.
       Optional; UMLS enrichment is skipped when not provided.
   * - ``--skip-ols``
     - flag
     - False
     - Skip the OLS4 ontology enrichment step.
   * - ``--skip-ncbi``
     - flag
     - False
     - Skip the NCBI Taxonomy enrichment step.
   * - ``--dry-run``
     - flag
     - False
     - Run all enrichment steps but do not write the output file. Useful
       for validating inputs and checking for collisions.
   * - ``--verbose-collisions``
     - flag
     - False
     - Print every term collision to stdout instead of only the summary count.

**Examples:**

.. code-block:: bash

   # Standard rebuild (overwrites bundled dictionary in place)
   biometaharmonizer build-dicts

   # Skip the network-heavy NCBI Taxonomy step
   biometaharmonizer build-dicts --skip-ncbi

   # Use a pre-downloaded taxdmp.zip to avoid the ~65 MB download
   biometaharmonizer build-dicts --taxdmp /path/to/taxdmp.zip

   # Write to a custom output path without touching the bundled file
   biometaharmonizer build-dicts \
       --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
       --output /data/my_custom_dictionaries.json

   # Dry-run with verbose collision output
   biometaharmonizer build-dicts --dry-run --verbose-collisions

----

run
---

Runs the full harmonization pipeline: ingest → key-map → date/geo/One Health
→ output.

**Usage:**

.. code-block:: bash

   biometaharmonizer run \
       --input   <FILE_OR_ACCESSIONS> \
       --email   <EMAIL> \
       --output  <FILE> \
       [--api-key <KEY>] \
       [--cache-dir <DIR>] \
       [--format <FORMAT> [<FORMAT> ...]] \
       [--summary <FILE>] \
       [--fetch-batch-size <N>] \
       [--esearch-batch-size <N>] \
       [--refresh-cache] \
       [--verbose]

**Input flexibility:**

The ``--input`` argument accepts:

- A path to a plain-text file containing one accession per line.
- A comma-separated list of accessions passed directly as a string
  (e.g. ``-i "SAMN02436525,SAMN02434874"``). This is detected automatically
  when the argument does not look like an existing file path.

Accepted accession prefixes: ``SAMN``, ``SAME``, ``SAMD`` (BioSample) or
``GCF_``, ``GCA_`` (assembly). Mixed files are handled automatically.

**Output format inference:**

If ``--format`` is not specified, the output format is inferred from the
file extension of ``--output``:

.. list-table:: Format inference from file extension
   :header-rows: 1

   * - Extension
     - Inferred format
   * - ``.csv``
     - ``csv``
   * - ``.tsv``
     - ``tsv``
   * - ``.txt``
     - ``tsv``
   * - ``.xlsx``
     - ``excel``
   * - ``.xls``
     - ``excel``
   * - ``.parquet``
     - ``parquet``
   * - ``.jsonl``
     - ``jsonl``
   * - (other)
     - ``csv``

**Multiple output formats:**

``--format`` accepts one or more space-separated format names. When a single
format is given the ``--output`` path is used as-is. When multiple formats
are given the stem of ``--output`` is reused and the correct extension is
appended for each format (e.g. ``--output out.csv --format csv tsv excel``
produces ``out.csv``, ``out.tsv``, ``out.xlsx``).

**Flags:**

.. list-table:: CLI flags
   :header-rows: 1

   * - Long flag
     - Short
     - Type
     - Default
     - Description
   * - ``--input``
     - ``-i``
     - str
     - —
     - **Required.** Input file or comma-separated accession list.
   * - ``--email``
     - ``-e``
     - str
     - —
     - **Required.** NCBI contact email.
   * - ``--output``
     - ``-o``
     - str
     - —
     - **Required.** Output file path.
   * - ``--api-key``
     - —
     - str
     - None
     - NCBI API key.
   * - ``--cache-dir``
     - —
     - str
     - None
     - Assembly summary cache directory.
   * - ``--format``
     - ``-f``
     - choice(s)
     - None
     - Output format(s): ``csv``, ``tsv``, ``excel``, ``parquet``, ``jsonl``.
       One or more space-separated values.
   * - ``--summary``
     - —
     - str
     - None
     - Write fill-rate summary CSV to this path.
   * - ``--fetch-batch-size``
     - —
     - int
     - 200
     - Records per efetch request (clamped to 500 maximum).
   * - ``--esearch-batch-size``
     - —
     - int
     - 200
     - Accessions per esearch term.
   * - ``--refresh-cache``
     - —
     - flag
     - False
     - Force re-download of assembly index.
   * - ``--verbose``
     - ``-v``
     - flag
     - False
     - Enable DEBUG-level logging.
   * - ``--version``
     - —
     - flag
     - —
     - Print version string and exit.

**Examples:**

.. code-block:: bash

   # BioSample file, CSV output with summary
   biometaharmonizer run \
       --input    biosample_ids.txt \
       --email    your@email.com \
       --api-key  abc123def456 \
       --output   harmonized.csv \
       --summary  fill_rates.csv \
       --verbose

   # Assembly accessions, Parquet output, custom cache
   biometaharmonizer run \
       --input           assemblies.txt \
       --email           your@email.com \
       --output          harmonized.parquet \
       --cache-dir       /data/bmh_cache \
       --fetch-batch-size 500 \
       --refresh-cache

   # Inline accessions (no file required)
   biometaharmonizer run \
       -i "SAMN02436525,SAMN02434874,SAMN02429261" \
       -e your@email.com \
       -o out.csv

   # Multiple output formats at once
   biometaharmonizer run \
       --input  ids.txt \
       --email  your@email.com \
       --output harmonized.csv \
       --format csv tsv excel jsonl

----

generate-report
---------------

Generates an HTML (and optionally PDF) summary report from a harmonized
output file produced by ``biometaharmonizer run``.

**Usage:**

.. code-block:: bash

   biometaharmonizer generate-report <INPUT> [<OUTPUT>]

**Arguments:**

.. list-table::
   :header-rows: 1

   * - Argument
     - Type
     - Default
     - Description
   * - ``INPUT``
     - str
     - —
     - **Required.** Path to the harmonized CSV/TSV/Parquet file.
   * - ``OUTPUT``
     - str
     - ``<INPUT stem>_report.html``
     - Output report path. The format is inferred from the extension
       (``.html`` or ``.pdf``). PDF export requires ``kaleido``
       (``pip install kaleido``).

**Examples:**

.. code-block:: bash

   # Generate HTML report (output defaults to harmonized_report.html)
   biometaharmonizer generate-report harmonized.csv

   # Specify output path explicitly
   biometaharmonizer generate-report harmonized.csv report.html

   # Generate PDF (requires kaleido)
   biometaharmonizer generate-report harmonized.csv report.pdf

----

Exit Codes
----------

.. list-table::
   :header-rows: 1

   * - Code
     - Subcommand(s)
     - Meaning
   * - ``0``
     - all
     - Success.
   * - ``1``
     - ``run``
     - User input error — invalid email, input file not found, unrecognized
       format string, or no valid BioSample IDs could be resolved.
   * - ``1``
     - ``build-dicts``, ``build-ncbi-cache``, ``generate-report``
     - User input error — missing or unreadable input file, invalid flag
       combination.
   * - ``2``
     - ``run``
     - Runtime error — ingestion failure (network, XML parse), empty
       DataFrame after ingestion, write failure, or import error.
   * - ``2``
     - ``build-dicts``, ``build-ncbi-cache``
     - Runtime error — network failure during download, parse error in
       upstream data, or write failure.

CLI Flag ↔ Python API Mapping
-------------------------------

.. list-table:: ``run`` flags mapped to ``ingest()`` parameters
   :header-rows: 1

   * - CLI flag
     - ``ingest()`` parameter
     - Default
   * - ``--input``
     - ``source``
     - —
   * - ``--email``
     - ``email``
     - —
   * - ``--api-key``
     - ``api_key``
     - ``None``
   * - ``--cache-dir``
     - ``cache_dir``
     - ``None``
   * - ``--fetch-batch-size``
     - ``fetch_batch_size``
     - 200
   * - ``--esearch-batch-size``
     - ``esearch_batch_size``
     - 200
   * - ``--refresh-cache``
     - ``refresh_cache``
     - ``False``

.. note::

   The CLI default for ``--esearch-batch-size`` is 200 (as declared in
   ``add_argument``), while the Python API module-level constant
   ``_ESEARCH_BATCH`` defaults to 100. The effective value is whichever
   is passed to ``ingest()``.

Log Output Format
-----------------

Log messages are written to ``stderr`` using the format:

.. code-block:: text

   HH:MM:SS  LEVEL    logger_name: message

For example:

.. code-block:: text

   14:32:01  INFO     biometaharmonizer.ingestion: Fetching NCBI assembly index (refseq) ...
   14:32:45  INFO     biometaharmonizer.ingestion: Fetching metadata for 1500 BioSample accessions...
   14:35:12  INFO     biometaharmonizer.ingestion: ============================================================
   14:35:12  INFO     biometaharmonizer.ingestion: INGEST SUMMARY
   14:35:12  INFO     biometaharmonizer.ingestion:   Input IDs provided  : 1500
   14:35:12  INFO     biometaharmonizer.ingestion:   fetch_batch_size    : 200
   14:35:12  INFO     biometaharmonizer.ingestion:   esearch_batch_size  : 200
   14:35:12  INFO     biometaharmonizer.ingestion:   Records in output   : 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   bioproject_accession filled : 1350 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_refseq   filled : 1200 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_genbank  filled : 1100 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion: ============================================================
   14:35:14  INFO     biometaharmonizer.cli: Writing output to harmonized.csv (format=csv)
   Done. 1498 records x 57 columns -> harmonized.csv

The final ``Done.`` line is printed to ``stdout``.
