"""
Command-line interface for BioMetaHarmonizer.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Absolute path to the bundled schemas directory — works regardless of cwd.
_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_VALID_FORMATS = ["csv", "tsv", "excel", "parquet", "jsonl"]

_FMT_TO_EXT = {
    "csv": ".csv",
    "tsv": ".tsv",
    "excel": ".xlsx",
    "parquet": ".parquet",
    "jsonl": ".jsonl",
}


def _lower_format(s: str) -> str:
    """Argparse type: normalise format string to lowercase and validate."""
    lowered = s.lower()
    if lowered not in _VALID_FORMATS:
        raise argparse.ArgumentTypeError(
            f"invalid format {s!r}. Choose from: {', '.join(_VALID_FORMATS)}"
        )
    return lowered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biometaharmonizer",
        description=(
            "Harmonize NCBI BioSample metadata: fetch, normalize column names, "
            "parse dates, resolve geography, classify One Health category, and export.\n\n"
            "Subcommands:\n"
            "  run                 Full harmonization pipeline\n"
            "  build-dicts         Rebuild one_health_dictionaries.json\n"
            "  build-ncbi-cache    Fetch NCBI BioSample attribute XML\n"
            "  generate-report     Generate interactive HTML report from output file"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── run ──────────────────────────────────────────────────────────────────
    run_p = sub.add_parser(
        "run",
        help="Full harmonization pipeline: ingest -> key-map -> date/geo/one-health -> output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run the full BioMetaHarmonizer pipeline on a list of BioSample or "
            "assembly accessions.\n\n"
            "Input file format: one accession per line.\n"
            "Accepted prefixes: SAMN/SAME/SAMD (BioSample) or GCF_/GCA_ (assembly).\n"
            "Mixed files are handled automatically.\n\n"
            "Examples:\n"
            "    biometaharmonizer run \\\n"
            "        --input ids.txt \\\n"
            "        --email your@email.com \\\n"
            "        --output harmonized.csv\n\n"
            "    # Save to multiple formats at once:\n"
            "    biometaharmonizer run \\\n"
            "        --input ids.txt \\\n"
            "        --email your@email.com \\\n"
            "        --output harmonized.csv \\\n"
            "        --format csv tsv excel"
        ),
    )

    run_p.add_argument("--input", "-i", required=True, metavar="FILE")
    run_p.add_argument("--email", "-e", required=True, metavar="EMAIL")
    run_p.add_argument("--output", "-o", required=True, metavar="FILE")
    run_p.add_argument("--api-key", metavar="KEY", default=None)
    run_p.add_argument("--cache-dir", metavar="DIR", default=None)
    run_p.add_argument(
        "--format", "-f",
        type=_lower_format,
        nargs="+",
        default=None,
        metavar="FORMAT",
        help=(
            "One or more output formats: csv, tsv, excel, parquet, jsonl "
            "(case-insensitive, space-separated). "
            "When a single format is given the output path is used as-is. "
            "When multiple formats are given the stem of --output is reused and "
            "the correct extension is appended for each format "
            "(e.g. --output out.csv --format csv tsv excel produces "
            "out.csv, out.tsv, out.xlsx). "
            "Omit to infer the format from the --output file extension."
        ),
    )
    run_p.add_argument("--summary", metavar="FILE", default=None)
    run_p.add_argument("--verbose", "-v", action="store_true", default=False)
    run_p.add_argument(
        "--fetch-batch-size",
        metavar="N",
        type=int,
        default=200,
        help="Records per efetch request (default: 200, max recommended: 500).",
    )
    run_p.add_argument(
        "--esearch-batch-size",
        metavar="N",
        type=int,
        default=200,
        help="Number of accessions per esearch term (default: 200).",
    )
    run_p.add_argument(
        "--refresh-cache",
        action="store_true",
        default=False,
        help=(
            "Force re-download of assembly summary flat files, ignoring their age. "
            "Use when NCBI has added new assemblies since the last run."
        ),
    )

    # ── build-dicts ───────────────────────────────────────────────────────────
    _default_dicts = str(_SCHEMAS_DIR / "one_health_dictionaries.json")

    bd_p = sub.add_parser(
        "build-dicts",
        help="Rebuild one_health_dictionaries.json from OLS4, NCBI Taxonomy, and UMLS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Build an enriched one_health_dictionaries.json by querying:\n"
            "  1. OLS4 API  - ENVO, FoodOn, UBERON, Plant Ontology\n"
            "  2. NCBI Taxonomy local dump - host_to_category entries\n"
            "  3. UMLS API  - synonym expansion (optional, requires API key)\n\n"
            "The hand-curated base file is loaded first; any key already present\n"
            "in the base always wins over ontology-derived data (merge strategy: base_wins).\n\n"
            "Examples:\n"
            "    biometaharmonizer build-dicts\n"
            "    biometaharmonizer build-dicts --skip-ncbi --dry-run\n"
            "    biometaharmonizer build-dicts --taxdmp /path/to/taxdmp.zip\n"
            "    biometaharmonizer build-dicts --umls-key YOUR_KEY --verbose-collisions"
        ),
    )
    bd_p.add_argument(
        "--base",
        default=_default_dicts,
        metavar="FILE",
        help=(
            "Path to the hand-curated base JSON "
            f"(default: {_default_dicts})."
        ),
    )
    bd_p.add_argument(
        "--output",
        default=_default_dicts,
        metavar="FILE",
        help=(
            "Destination path for the enriched JSON "
            "(default: same as --base)."
        ),
    )
    bd_p.add_argument(
        "--taxdmp",
        default=None,
        metavar="PATH",
        help=(
            "Path to a pre-downloaded taxdmp.zip or an extracted directory "
            "containing names.dmp and nodes.dmp. "
            "If omitted, taxdmp.zip is downloaded automatically from NCBI FTP."
        ),
    )
    bd_p.add_argument(
        "--umls-key",
        dest="umls_key",
        default=None,
        metavar="KEY",
        help="UMLS API key for synonym expansion (optional).",
    )
    bd_p.add_argument("--skip-ols",  action="store_true", help="Skip OLS4 queries.")
    bd_p.add_argument("--skip-ncbi", action="store_true", help="Skip NCBI Taxonomy dump.")
    bd_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all enrichment steps but do not write the output file.",
    )
    bd_p.add_argument(
        "--verbose-collisions",
        action="store_true",
        help=(
            "Print per-term collision detail (COLLISION, PRIORITY, "
            "FOOD-HOST-PRIORITY lines). Silent by default."
        ),
    )

    # ── build-ncbi-cache ──────────────────────────────────────────────────────
    bn_p = sub.add_parser(
        "build-ncbi-cache",
        help="Fetch the NCBI BioSample attribute XML and save it to schemas/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fetches the NCBI BioSample attribute harmonization table and saves\n"
            "ncbi_attributes.xml to the bundled schemas/ directory.\n\n"
            "Run once after cloning, and re-run periodically to pick up new NCBI\n"
            "attribute definitions.\n\n"
            "Examples:\n"
            "    biometaharmonizer build-ncbi-cache\n"
            "    biometaharmonizer build-ncbi-cache --output-dir /tmp/schemas\n"
            "    biometaharmonizer build-ncbi-cache --skip-fetch"
        ),
    )
    bn_p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=str(_SCHEMAS_DIR),
        metavar="DIR",
        help=(
            "Directory to write ncbi_attributes.xml into "
            f"(default: {_SCHEMAS_DIR})."
        ),
    )
    bn_p.add_argument(
        "--skip-fetch",
        action="store_true",
        help=(
            "Skip the network request and only validate/report an existing "
            "ncbi_attributes.xml in the output directory."
        ),
    )

    # ── generate-report ───────────────────────────────────────────────────────
    gr_p = sub.add_parser(
        "generate-report",
        help="Generate an interactive self-contained HTML report from a harmonized output file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generates an interactive, self-contained HTML report from a\n"
            "BioMetaHarmonizer output file (CSV, TSV, Excel, or Parquet).\n\n"
            "The report includes fill-rate completeness, geographic distribution,\n"
            "temporal trends, taxonomy, One Health breakdown, and a searchable\n"
            "data table - all embedded in a single HTML file.\n\n"
            "Examples:\n"
            "    biometaharmonizer generate-report harmonized.csv\n"
            "    biometaharmonizer generate-report harmonized.csv report.html\n"
            "    biometaharmonizer generate-report harmonized.parquet"
        ),
    )
    gr_p.add_argument(
        "input",
        metavar="INPUT",
        help="Path to a BioMetaHarmonizer output file (.csv, .tsv, .xlsx, .parquet).",
    )
    gr_p.add_argument(
        "output",
        nargs="?",
        default=None,
        metavar="OUTPUT",
        help="Output HTML path (default: <input_stem>_report.html next to the input file).",
    )

    parser.add_argument("--version", action="version", version="%(prog)s " + _get_version())
    return parser


def _get_version() -> str:
    try:
        from biometaharmonizer import __version__
        return __version__
    except ImportError:
        return "unknown"


def _infer_format(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".txt": "tsv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".parquet": "parquet",
        ".jsonl": "jsonl",
    }
    return mapping.get(suffix, "csv")


def _resolve_output_targets(output_path: Path, formats: list) -> list:
    """
    Return a list of (fmt, resolved_path) pairs.

    - Single format: use output_path unchanged.
    - Multiple formats: replace the extension of output_path with the
      canonical extension for each format.
    """
    if len(formats) == 1:
        return [(formats[0], output_path)]
    stem = output_path.parent / output_path.stem
    return [(fmt, Path(str(stem) + _FMT_TO_EXT[fmt])) for fmt in formats]


def _looks_like_filepath(s: str) -> bool:
    accession_prefixes = ("SAMN", "SAME", "SAMD", "GCF_", "GCA_")
    last_part = Path(s).name
    has_extension = "." in last_part
    looks_like_accession = any(s.upper().startswith(p) for p in accession_prefixes)
    return has_extension and not looks_like_accession


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher helpers
# ─────────────────────────────────────────────────────────────────────────────

def _import_script(script_name: str):
    """
    Import a module from the scripts/ directory that sits next to src/.
    Inserts the scripts directory into sys.path once so subsequent imports
    are free.  Works regardless of how the package was installed.
    """
    scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import importlib
    return importlib.import_module(script_name)


def _run_build_dicts(args: argparse.Namespace) -> int:
    """
    Delegate to scripts/build_dictionaries.py::main() by reconstructing
    the argv list from the already-parsed namespace.  The script's
    parse_args(argv) signature accepts an explicit argv so sys.argv is
    never touched.
    """
    argv: list[str] = []
    argv += ["--base",   args.base]
    argv += ["--output", args.output]
    if args.taxdmp:
        argv += ["--taxdmp", args.taxdmp]
    if args.umls_key:
        argv += ["--umls-key", args.umls_key]
    if args.skip_ols:
        argv.append("--skip-ols")
    if args.skip_ncbi:
        argv.append("--skip-ncbi")
    if args.dry_run:
        argv.append("--dry-run")
    if args.verbose_collisions:
        argv.append("--verbose-collisions")

    mod = _import_script("build_dictionaries")
    mod.main(argv)
    return 0


def _run_build_ncbi_cache(args: argparse.Namespace) -> int:
    """
    Delegate to scripts/build_ncbi_attribute_cache.py::main().
    """
    argv: list[str] = []
    if args.output_dir:
        argv += ["--output-dir", args.output_dir]
    if args.skip_fetch:
        argv.append("--skip-fetch")

    mod = _import_script("build_ncbi_attribute_cache")
    mod.main(argv)
    return 0


def _run_generate_report(args: argparse.Namespace) -> int:
    """
    Delegate to scripts/generate_summary_report.py::generate_report()
    directly (no argv reconstruction needed - the function already takes
    explicit input/output parameters).
    """
    mod = _import_script("generate_summary_report")
    mod.generate_report(args.input, args.output)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# run subcommand
# ─────────────────────────────────────────────────────────────────────────────

def _run(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("biometaharmonizer.cli")

    input_arg = args.input.strip()
    input_path = Path(input_arg)

    if input_path.exists():
        source = input_path
    elif _looks_like_filepath(input_arg):
        print(f"ERROR: Input file not found: '{input_arg}'", file=sys.stderr)
        return 1
    else:
        accessions = [a.strip() for a in input_arg.split(",") if a.strip()]
        if not accessions:
            print(
                f"ERROR: --input '{input_arg}' is neither an existing file nor a valid comma-separated accession list.",
                file=sys.stderr,
            )
            return 1
        source = accessions

    output_path = Path(args.output)

    if args.format:
        targets = _resolve_output_targets(output_path, args.format)
    else:
        fmt = _infer_format(output_path)
        targets = [(fmt, output_path)]

    try:
        from biometaharmonizer.ingestion import set_email, ingest
        from biometaharmonizer.key_mapper import KeyMapper
        from biometaharmonizer.date_engine import DateEngine
        from biometaharmonizer.geo_engine import GeoEngine
        from biometaharmonizer.one_health import OneHealthClassifier
        from biometaharmonizer.output import write, write_summary
    except (ImportError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        set_email(args.email)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    kwargs = {}
    if args.api_key:
        kwargs["api_key"] = args.api_key
    if args.cache_dir:
        kwargs["cache_dir"] = args.cache_dir

    logger.info("Step 1/5  Ingestion")
    logger.info(
        "         fetch_batch_size=%d  esearch_batch_size=%d  refresh_cache=%s",
        args.fetch_batch_size, args.esearch_batch_size, args.refresh_cache,
    )
    t0 = time.perf_counter()
    try:
        df = ingest(
            source,
            fetch_batch_size=args.fetch_batch_size,
            esearch_batch_size=args.esearch_batch_size,
            refresh_cache=args.refresh_cache,
            **kwargs,
        )
    except Exception as exc:
        print(f"ERROR during ingestion: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2

    if df.empty:
        print("ERROR: Ingestion returned an empty DataFrame.", file=sys.stderr)
        return 2

    logger.info("         %d records ingested in %.1fs.", len(df), time.perf_counter() - t0)

    logger.info("Step 2/5  Key harmonization")
    t0 = time.perf_counter()
    try:
        mapper = KeyMapper()
        df = mapper.map_columns(df)
    except Exception as exc:
        print(f"ERROR during key harmonization: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2
    logger.info("         Done in %.1fs.", time.perf_counter() - t0)

    if "collection_date" in df.columns:
        logger.info("Step 3/5  Date parsing")
        t0 = time.perf_counter()
        de = DateEngine()
        date_df = de.parse_with_range(df["collection_date"])
        df["collection_date"] = date_df["collection_date"]
        if "collection_date_range" in date_df.columns:
            df["collection_date_range"] = date_df["collection_date_range"]
        parsed = df["collection_date"].notna().sum()
        logger.info(
            "         %d / %d dates parsed in %.1fs.",
            parsed, len(df), time.perf_counter() - t0,
        )
    else:
        logger.info("Step 3/5  Date parsing skipped (no collection_date column).")

    if "geo_loc_name" in df.columns:
        logger.info("Step 4/5  Geospatial parsing")
        t0 = time.perf_counter()
        ge = GeoEngine()
        geo_df = ge.parse(df["geo_loc_name"])
        for col in geo_df.columns:
            df[col] = geo_df[col]
        resolved = df["geo_country"].notna().sum() if "geo_country" in df.columns else 0
        logger.info(
            "         %d / %d geo_loc_name values resolved to country in %.1fs.",
            resolved, len(df), time.perf_counter() - t0,
        )
    else:
        logger.info("Step 4/5  Geospatial parsing skipped (no geo_loc_name column).")

    logger.info("Step 5/5  One Health classification")
    t0 = time.perf_counter()
    classifier = OneHealthClassifier()
    src_cols = ["isolation_source", "env_broad_scale", "env_local_scale", "env_medium", "sample_type", "host"]
    present = {col: df[col] for col in src_cols if col in df.columns}

    if present:
        try:
            oh_df = classifier.classify_multi_field(**present)
            for col in oh_df.columns:
                df[col] = oh_df[col]
            logger.info(
                "         %d source fields used; done in %.1fs.",
                len(present), time.perf_counter() - t0,
            )
        except Exception as exc:
            logger.warning("Extended classification failed (%s); falling back to legacy joint mode.", exc)
            if "isolation_source" in df.columns and "host" in df.columns:
                df["one_health_category"] = classifier.classify_joint(
                    df["isolation_source"], df["host"]
                )
            elif "isolation_source" in df.columns:
                df["one_health_category"] = classifier.classify(df["isolation_source"])
    else:
        logger.info("         One Health skipped (no source columns present).")

    if "one_health_category" in df.columns:
        classified = (df["one_health_category"].notna() & (df["one_health_category"] != "Unclassified")).sum()
        unclassified = len(df) - classified
        logger.info(
            "         %d / %d records classified (%d unclassified).",
            classified, len(df), unclassified,
        )

    written_paths = []
    for fmt, out_path in targets:
        logger.info("Writing output to %s (format=%s)", out_path, fmt)
        t0 = time.perf_counter()
        try:
            write(df, out_path, fmt=fmt)
            written_paths.append(out_path)
        except Exception as exc:
            print(f"ERROR writing output ({fmt}): {exc}", file=sys.stderr)
            logger.debug("", exc_info=True)
            return 2
        logger.info("         Written in %.1fs.", time.perf_counter() - t0)

    if args.summary:
        summary_path = Path(args.summary)
        logger.info("Writing fill-rate summary to %s", summary_path)
        try:
            write_summary(df, summary_path)
        except Exception as exc:
            print(f"ERROR writing summary: {exc}", file=sys.stderr)
            logger.debug("", exc_info=True)
            return 2

    paths_str = ", ".join(str(p) for p in written_paths)
    print(f"Done. {len(df)} records x {len(df.columns)} columns -> {paths_str}", file=sys.stdout)
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "run":               _run,
        "build-dicts":       _run_build_dicts,
        "build-ncbi-cache":  _run_build_ncbi_cache,
        "generate-report":   _run_generate_report,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args))


if __name__ == "__main__":
    main()
