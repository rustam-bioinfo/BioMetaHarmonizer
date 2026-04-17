"""
Command-line interface for BioMetaHarmonizer.

Usage
-----
    biometaharmonizer run \\
        --input  accessions.txt \\
        --email  your@email.com \\
        --output results/harmonized.csv

    biometaharmonizer run \\
        --input  accessions.txt \\
        --email  your@email.com \\
        --api-key <NCBI_API_KEY> \\
        --output results/harmonized.csv \\
        --format parquet \\
        --cache-dir /tmp/bmh_cache \\
        --drop-sparse 5 \\
        --no-drop-junk \\
        --summary results/fill_rates.csv

Exit codes
----------
    0  Success
    1  User error (bad arguments, missing file)
    2  Runtime error (fetch failure, schema missing)
"""

import argparse
import logging
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biometaharmonizer",
        description=(
            "Harmonize NCBI BioSample metadata: fetch, normalize column names, "
            "parse dates, resolve geography, classify One Health category, and export."
        ),
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ------------------------------------------------------------------ run --
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
            "Example:\n"
            "    biometaharmonizer run \\\n"
            "        --input ids.txt \\\n"
            "        --email your@email.com \\\n"
            "        --output harmonized.csv"
        ),
    )

    # Required
    run_p.add_argument(
        "--input", "-i",
        required=True,
        metavar="FILE",
        help="Path to a plain-text file with one accession per line, "
             "or a comma-separated list of accessions passed as a string.",
    )
    run_p.add_argument(
        "--email", "-e",
        required=True,
        metavar="EMAIL",
        help="E-mail address required by NCBI Entrez for all API calls.",
    )
    run_p.add_argument(
        "--output", "-o",
        required=True,
        metavar="FILE",
        help="Output file path.  Format is inferred from the extension "
             "(.csv, .tsv, .xlsx, .parquet) unless --format is given.",
    )

    # Optional -- ingestion
    run_p.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="NCBI API key (raises rate limit from 3 to 10 req/s). "
             "Register free at https://www.ncbi.nlm.nih.gov/account/",
    )
    run_p.add_argument(
        "--cache-dir",
        metavar="DIR",
        default=None,
        help="Directory for NCBI assembly summary flat-file cache "
             "(default: ~/.biometaharmonizer/cache/).",
    )

    # Optional -- key mapper
    run_p.add_argument(
        "--model",
        metavar="MODEL",
        default=None,
        help=(
            "sentence-transformers model for Layer 2 semantic column matching. "
            "Defaults to the model recorded in ncbi_cache_meta.json "
            "(written by build_ncbi_attribute_cache.py), which is "
            "'all-MiniLM-L6-v2' unless the cache was rebuilt with a different model. "
            "The model you pass MUST match the one used to build ncbi_embeddings.npy. "
            "Examples: 'all-mpnet-base-v2', 'BAAI/bge-small-en-v1.5', "
            "'intfloat/e5-small-v2'."
        ),
    )
    run_p.add_argument(
        "--threshold",
        type=float,
        metavar="FLOAT",
        default=None,
        help="Cosine similarity threshold for Layer 2 acceptance (default: 0.75). "
             "Lower values increase recall at the cost of precision.",
    )
    run_p.add_argument(
        "--drop-sparse",
        type=float,
        metavar="N",
        default=5,
        help="Drop columns whose non-null count falls below this threshold. "
             "An integer value (e.g. 5) is treated as an absolute row count. "
             "A value between 0 and 1 (e.g. 0.05) is treated as a fractional "
             "fill rate and drops columns with less than that fraction of "
             "non-null values. Set to 0 to disable (default: 5).",
    )
    run_p.add_argument(
        "--no-drop-junk",
        action="store_true",
        default=False,
        help="Disable removal of submitter-artifact columns "
             "(person names, email addresses used as keys).",
    )

    # Optional -- output
    run_p.add_argument(
        "--format", "-f",
        choices=["csv", "tsv", "excel", "parquet"],
        default=None,
        metavar="FORMAT",
        help="Output format: csv, tsv, excel, parquet. "
             "Inferred from file extension when omitted.",
    )
    run_p.add_argument(
        "--summary",
        metavar="FILE",
        default=None,
        help="Optional path to write a column fill-rate summary CSV.",
    )

    # Optional -- pipeline switches
    run_p.add_argument(
        "--skip-dates",
        action="store_true",
        default=False,
        help="Skip date parsing (collection_date column left as-is).",
    )
    run_p.add_argument(
        "--skip-geo",
        action="store_true",
        default=False,
        help="Skip geospatial parsing (geo_loc_name column left as-is).",
    )
    run_p.add_argument(
        "--skip-one-health",
        action="store_true",
        default=False,
        help="Skip One Health classification.",
    )

    # Optional -- logging
    run_p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )

    # ---------------------------------------------------------------- version -
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + _get_version(),
    )

    return parser


def _get_version() -> str:
    try:
        from biometaharmonizer import __version__
        return __version__
    except ImportError:
        return "unknown"


def _infer_format(path: Path) -> str:
    """Infer output format from file extension."""
    suffix = path.suffix.lower()
    mapping = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".txt": "tsv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".parquet": "parquet",
    }
    return mapping.get(suffix, "csv")


def _looks_like_filepath(s: str) -> bool:
    """
    Return True when s appears to be a file path rather than an accession string.
    Heuristic: the last path component contains a dot (i.e. has an extension)
    AND the string does not match the known accession prefixes.
    This catches 'accessions.txt', 'data/ids.csv', etc.
    """
    accession_prefixes = ("SAMN", "SAME", "SAMD", "GCF_", "GCA_")
    last_part = Path(s).name
    has_extension = "." in last_part
    looks_like_accession = any(s.upper().startswith(p) for p in accession_prefixes)
    return has_extension and not looks_like_accession


def _run(args: argparse.Namespace) -> int:
    """Execute the full harmonization pipeline."""
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("biometaharmonizer.cli")

    # ---- resolve input -------------------------------------------------------
    input_arg = args.input.strip()
    input_path = Path(input_arg)

    if input_path.exists():
        source = input_path
        logger.debug("Input: file %s", source)
    elif _looks_like_filepath(input_arg):
        # Looks like a filename but does not exist -- fail clearly.
        print(
            f"ERROR: Input file not found: '{input_arg}'",
            file=sys.stderr,
        )
        return 1
    else:
        # Treat as comma-separated accession list.
        accessions = [a.strip() for a in input_arg.split(",") if a.strip()]
        if not accessions:
            print(
                f"ERROR: --input '{input_arg}' is neither an existing file "
                "nor a valid comma-separated accession list.",
                file=sys.stderr,
            )
            return 1
        source = accessions
        logger.debug("Input: %d accession(s) from command line", len(accessions))

    output_path = Path(args.output)
    fmt = args.format or _infer_format(output_path)

    # ---- imports (deferred to keep --help fast) ------------------------------
    try:
        from biometaharmonizer.ingestion import set_email, ingest
        from biometaharmonizer.key_mapper import KeyMapper
        from biometaharmonizer.date_engine import DateEngine
        from biometaharmonizer.geo_engine import GeoEngine
        from biometaharmonizer.one_health import OneHealthClassifier
        from biometaharmonizer.output import write, write_summary
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # ---- 1. Ingest -----------------------------------------------------------
    set_email(args.email)
    kwargs = {}
    if args.api_key:
        kwargs["api_key"] = args.api_key
    if args.cache_dir:
        kwargs["cache_dir"] = args.cache_dir

    logger.info("Step 1/5  Ingestion")
    try:
        df = ingest(source, **kwargs)
    except Exception as exc:
        print(f"ERROR during ingestion: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2

    if df.empty:
        print("ERROR: Ingestion returned an empty DataFrame.", file=sys.stderr)
        return 2

    logger.info("         %d records, %d columns ingested.", len(df), len(df.columns))

    # ---- 2. Key harmonization ------------------------------------------------
    logger.info("Step 2/5  Key harmonization")
    km_kwargs = {}
    if args.model:
        km_kwargs["model"] = args.model
    if args.threshold is not None:
        km_kwargs["threshold"] = args.threshold
    try:
        mapper = KeyMapper(**km_kwargs)
        df = mapper.map_columns(
            df,
            drop_sparse=args.drop_sparse,
            drop_junk=not args.no_drop_junk,
        )
    except RuntimeError as exc:
        print(f"ERROR during key harmonization: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2

    logger.info("         %d columns after harmonization.", len(df.columns))
    if hasattr(mapper, "compliance_report") and not mapper.compliance_report.empty:
        fail_rows = mapper.compliance_report[mapper.compliance_report["status"] == "FAIL"]
        if not fail_rows.empty:
            logger.warning(
                "Mandatory field compliance FAILs:\n%s",
                fail_rows.to_string(index=False),
            )

    # ---- 3. Date parsing -----------------------------------------------------
    if not args.skip_dates and "collection_date" in df.columns:
        logger.info("Step 3/5  Date parsing")
        de = DateEngine()
        date_df = de.parse_with_range(df["collection_date"])
        df["collection_date"] = date_df["collection_date"]
        if "collection_date_range" in date_df.columns:
            df["collection_date_range"] = date_df["collection_date_range"]
        parsed = df["collection_date"].notna().sum()
        logger.info("         %d / %d dates parsed.", parsed, len(df))
    else:
        if args.skip_dates:
            logger.info("Step 3/5  Date parsing skipped (--skip-dates).")
        else:
            logger.info("Step 3/5  Date parsing skipped (no collection_date column).")

    # ---- 4. Geography --------------------------------------------------------
    if not args.skip_geo and "geo_loc_name" in df.columns:
        logger.info("Step 4/5  Geospatial parsing")
        ge = GeoEngine()
        geo_df = ge.parse(df["geo_loc_name"])
        new_geo_cols = [c for c in geo_df.columns if c not in df.columns]
        df = df.join(geo_df[new_geo_cols])
        resolved = df["geo_country"].notna().sum() if "geo_country" in df.columns else 0
        logger.info("         %d / %d geo_loc_name values resolved to country.", resolved, len(df))
    else:
        if args.skip_geo:
            logger.info("Step 4/5  Geospatial parsing skipped (--skip-geo).")
        else:
            logger.info("Step 4/5  Geospatial parsing skipped (no geo_loc_name column).")

    # ---- 5. One Health -------------------------------------------------------
    if not args.skip_one_health:
        classifier = OneHealthClassifier()
        if "isolation_source" in df.columns and "host" in df.columns:
            logger.info("Step 5/5  One Health classification (joint mode)")
            df["one_health_category"] = classifier.classify_joint(
                df["isolation_source"], df["host"]
            )
        elif "isolation_source" in df.columns:
            logger.info("Step 5/5  One Health classification (isolation_source only)")
            df["one_health_category"] = classifier.classify(df["isolation_source"])
        else:
            logger.info("Step 5/5  One Health skipped (no isolation_source column).")
        if "one_health_category" in df.columns:
            classified = df["one_health_category"].notna().sum()
            logger.info(
                "         %d / %d records classified.", classified, len(df)
            )
    else:
        logger.info("Step 5/5  One Health skipped (--skip-one-health).")

    # ---- Output --------------------------------------------------------------
    logger.info("Writing output to %s (format=%s)", output_path, fmt)
    try:
        write(df, output_path, fmt=fmt)
    except Exception as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2

    if args.summary:
        summary_path = Path(args.summary)
        logger.info("Writing fill-rate summary to %s", summary_path)
        try:
            write_summary(df, summary_path)
        except Exception as exc:
            print(f"ERROR writing summary: {exc}", file=sys.stderr)
            logger.debug("", exc_info=True)
            return 2

    print(
        f"Done. {len(df)} records x {len(df.columns)} columns -> {output_path}",
        file=sys.stdout,
    )
    return 0


def main() -> None:
    """Entry point registered in pyproject.toml [project.scripts]."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        sys.exit(_run(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
