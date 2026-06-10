.. _changelog:

=========
Changelog
=========

All notable changes to BioMetaHarmonizer are documented in this file.
Format follows `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.

----

v1.1.0 — 2026-06-10
---------------------

**Added**

- ``biometaharmonizer build-dicts`` CLI subcommand — replaces direct
  invocation of ``scripts/build_dictionaries.py``. Accepts the same flags
  (``--base``, ``--output``, ``--taxdmp``, ``--umls-key``, ``--skip-ols``,
  ``--skip-ncbi``, ``--dry-run``, ``--verbose-collisions``).
- ``biometaharmonizer build-ncbi-cache`` CLI subcommand — replaces direct
  invocation of ``scripts/build_ncbi_attribute_cache.py``. Accepts
  ``--output-dir`` and ``--skip-fetch``.
- ``biometaharmonizer generate-report`` CLI subcommand — replaces direct
  invocation of ``scripts/generate_summary_report.py``. Generates
  self-contained HTML (and optionally PDF) summary reports from a
  harmonized output file.

**Changed**

- ``docs/installation.rst``: added *Post-install Setup* section documenting
  the required ``build-ncbi-cache`` + ``build-dicts`` one-time setup step.
- ``docs/quickstart.rst``: added *Before Your First Run* section at the top.
- ``docs/cli_reference.rst``: reordered subcommands to reflect workflow order
  (setup → run → report); added full flag tables and examples for all three
  new subcommands; expanded exit code table.
- ``docs/faq.rst``: updated FAQ #13 and #14 to use ``biometaharmonizer``
  subcommands instead of ``python scripts/``; added FAQ #15
  (``build-ncbi-cache``), FAQ #16 (``generate-report``); renumbered
  thread-safety entry to #17.

----

v1.0.0 — 2026-05-12
---------------------

First stable release. All known bugs and robustness issues have been resolved
before this milestone.
