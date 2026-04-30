.. _cli_reference:

=============
CLI Reference
=============

BioMetaHarmonizer installs a ``biometaharmonizer`` entry point that is
registered in ``pyproject.toml`` as::

    biometaharmonizer = "biometaharmonizer.cli:main"

The CLI is built with ``argparse`` and exposes a single subcommand: ``run``.

.. code-block:: bash

   biometaharmonizer --version
   biometaharmonizer run --help

run subcommand
--------------

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
       [--format <FORMAT>] \
       [--summary <FILE>] \
       [--fetch-batch-size <N>] \
       [--esearch-batch-size <N>] \
       [--refresh-cache] \
       [--verbose]

**Input flexibility:**

The ``--input`` argument accepts:

- A path to a plain-text file containing one accession per line.
- A comma-separated list of accessions passed directly as a string.

Accepted accession prefixes: ``SAMN``, ``SAME``, ``SAMD`` (BioSample) or
``GCF_``, ``GCA_`` (assembly). Mixed files are handled automatically.

**Output format inference:**

If ``--format`` is not specified, the output format is inferred from the
file extension of ``--output``:

.. list-table:: CLI flags
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
     - | ``parquet``
   * - (other)
     - ``csv``

Flags
-----

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
     - choice
     - None
     - Output format: ``csv``, ``tsv``, ``excel``, ``parquet``.
   * - ``--summary``
     - —
     - str
     - None
     - Write fill-rate summary CSV to this path.
   * - ``--fetch-batch-size``
     - —
     - int
     - 200
     - Records per efetch request.
   * - ``--esearch-batch-size``
     - | —
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

CLI Flag ↔ Python API Mapping
-------------------------------

.. list-table:: CLI flags
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
     - | ``esearch_batch_size``
     - 100
   * - ``--refresh-cache``
     - ``refresh_cache``
     - ``False``

.. note::

   The CLI default for ``--esearch-batch-size`` is 200 (as declared in
   ``add_argument``), while the Python API module-level constant
   ``_ESEARCH_BATCH`` defaults to 100. The effective value is whichever
   is passed to ``ingest()``.

Complete Invocation Examples
-----------------------------

**Example 1 — BioSample file, CSV output with summary:**

.. code-block:: bash

   biometaharmonizer run \
       --input    biosample_ids.txt \
       --email    your@email.com \
       --api-key  abc123def456 \
       --output   harmonized.csv \
       --summary  fill_rates.csv \
       --verbose

**Example 2 — Assembly accessions, Parquet output, custom cache:**

.. code-block:: bash

   biometaharmonizer run \
       --input           assemblies.txt \
       --email           your@email.com \
       --output          harmonized.parquet \
       --cache-dir       /data/bmh_cache \
       --fetch-batch-size 500 \
       --refresh-cache

**Example 3 — Inline accessions (no file required):**

.. code-block:: bash

   biometaharmonizer run \
       -i "SAMN02436525,SAMN02434874,SAMN02429261" \
       -e your@email.com \
       -o out.csv

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
   14:35:12  INFO     biometaharmonizer.ingestion:   esearch_batch_size  : 100
   14:35:12  INFO     biometaharmonizer.ingestion:   Records in output   : 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   bioproject_accession filled : 1350 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_refseq   filled : 1200 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_genbank  filled : 1100 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion: ============================================================
   14:35:14  INFO     biometaharmonizer.cli: Writing output to harmonized.csv (format=csv)
   Done. 1498 records x 51 columns -> harmonized.csv

The final ``Done.`` line is printed to ``stdout``.