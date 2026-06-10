.. _installation:

============
Installation
============

Requirements
------------

BioMetaHarmonizer requires **Python 3.9 or later**.

PyPI Installation
-----------------

Install the latest stable release with pip:

.. code-block:: bash

   pip install biometaharmonizer

Development Install from Source
--------------------------------

Clone the repository and install in editable mode:

.. code-block:: bash

   git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
   cd BioMetaHarmonizer
   pip install -e .

The following dependencies are declared in ``pyproject.toml`` and are
installed automatically:

+----------------------+-----------------+
| Package              | Minimum version |
+======================+=================+
| pandas               | >=1.5           |
+----------------------+-----------------+
| numpy                | >=1.24          |
+----------------------+-----------------+
| biopython            | >=1.80          |
+----------------------+-----------------+
| requests             | >=2.28          |
+----------------------+-----------------+
| pycountry            | >=22.3          |
+----------------------+-----------------+
| python-dateutil      | >=2.8           |
+----------------------+-----------------+
| openpyxl             | >=3.0           |
+----------------------+-----------------+
| pyarrow              | >=12.0          |
+----------------------+-----------------+
| rapidfuzz            | >=3.0.0         |
+----------------------+-----------------+

``rapidfuzz`` is a required runtime dependency that enables the fuzzy-matching
fallback layer in :class:`biometaharmonizer.one_health.OneHealthClassifier`.
If it is absent at import time the classifier logs a warning and disables the
fuzzy layer; all other functionality remains available.

``openpyxl`` is required only when writing Excel output (``fmt="excel"``).
``pyarrow`` is required only when writing Parquet output (``fmt="parquet"``).
``kaleido`` is an optional dependency of ``biometaharmonizer generate-report``
for PDF export; install it separately with ``pip install kaleido`` if needed.

Post-install Setup
------------------

After installing, run these two commands once to build the schema files
required for One Health classification:

.. code-block:: bash

   biometaharmonizer build-ncbi-cache
   biometaharmonizer build-dicts

``build-ncbi-cache`` downloads the NCBI BioSample attribute definitions XML
and saves it to the ``schemas/`` directory. ``build-dicts`` uses that cache
(along with OLS4 ontology data and NCBI Taxonomy) to build the enriched
``one_health_dictionaries.json`` file that drives classification.

Re-run these commands whenever you want to refresh the bundled dictionaries
from upstream NCBI or OLS sources.

.. note::

   Both commands are safe to re-run at any time. Existing schema files are
   overwritten in place; your harmonization results are not affected until
   the next ``biometaharmonizer run`` call.

NCBI API Key
------------

All Entrez requests require a contact e-mail address. Without an API key,
NCBI enforces a rate limit of **3 requests per second**. With a free API key
the limit rises to **10 requests per second**, which roughly triples throughput
on large jobs.

To register a free API key:

1. Create or log in to your NCBI account at https://www.ncbi.nlm.nih.gov/account/
2. Navigate to **Settings → API Key Management**.
3. Click **Create an API Key** and copy the generated string.

Pass the key to :func:`biometaharmonizer.ingestion.set_api_key` before calling
:func:`biometaharmonizer.ingestion.ingest`, or supply it directly as the
``api_key`` argument:

.. code-block:: python

   import biometaharmonizer as bmh
   bmh.set_api_key("YOUR_API_KEY_HERE")

Cache Directory
---------------

Assembly summary flat files (approximately 100 MB each; two files are
downloaded — one for RefSeq, one for GenBank) are cached locally.

**Default location:** ``~/.biometaharmonizer/cache/``

This is the value of the module-level constant
:attr:`biometaharmonizer.ingestion.CACHE_DIR`.

**Files stored in the cache:**

- ``assembly_summary_refseq.txt``  — NCBI RefSeq assembly summary
- ``assembly_summary_genbank.txt`` — NCBI GenBank assembly summary

**Time-to-live (TTL):** Cache files older than **7 days** (the value of
``_CACHE_TTL_DAYS``) are automatically re-downloaded on the next
:func:`~biometaharmonizer.ingestion.ingest` call. You can also force
an immediate refresh with ``refresh_cache=True``.

To override the default cache location, call
:func:`biometaharmonizer.ingestion.set_cache_dir` before ingestion:

.. code-block:: python

   import biometaharmonizer as bmh
   # For Google Colab — use the Colab working directory
   bmh.set_cache_dir("/content/bmh_cache")

.. note::

   In Google Colab the home directory ``~/`` is the *root* of the VM
   filesystem. Setting the cache to ``/content`` or a subdirectory keeps
   the files inside your mounted Google Drive or the session's writable
   working directory.
