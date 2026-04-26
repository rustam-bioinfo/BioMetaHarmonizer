"""
Command-line interface for BioMetaHarmonizer.
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

    run_p.add_argument("--input", "-i", required=True, metavar="FILE")
    run_p.add_argument("--email", "-e", required=True, metavar="EMAIL")
    run_p.add_argument("--output", "-o", required=True, metavar="FILE")
    run_p.add_argument("--api-key", metavar="KEY", default=None)
    run_p.add_argument("--cache-dir", metavar="DIR", default=None)
    run_p.add_argument("--format", "-f", choices=["csv", "tsv", "excel", "parquet"], default=None, metavar="FORMAT")
    run_p.add_argument("--summary", metavar="FILE", default=None)
    run_p.add_argument("--verbose", "-v", action="store_true", default=False)

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
    }
    return mapping.get(suffix, "csv")


def _looks_like_filepath(s: str) -> bool:
    accession_prefixes = ("SAMN", "SAME", "SAMD", "GCF_", "GCA_")
    last_part = Path(s).name
    has_extension = "." in last_part
    looks_like_accession = any(s.upper().startswith(p) for p in accession_prefixes)
    return has_extension and not looks_like_accession


def _run(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")
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
    fmt = args.format or _infer_format(output_path)

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

    logger.info("         %d records ingested.", len(df))

    logger.info("Step 2/5  Key harmonization")
    try:
        mapper = KeyMapper()
        df = mapper.map_columns(df)
    except Exception as exc:
        print(f"ERROR during key harmonization: {exc}", file=sys.stderr)
        logger.debug("", exc_info=True)
        return 2

    if "collection_date" in df.columns:
        logger.info("Step 3/5  Date parsing")
        de = DateEngine()
        date_df = de.parse_with_range(df["collection_date"])
        df["collection_date"] = date_df["collection_date"]
        if "collection_date_range" in date_df.columns:
            df["collection_date_range"] = date_df["collection_date_range"]
        parsed = df["collection_date"].notna().sum()
        logger.info("         %d / %d dates parsed.", parsed, len(df))
    else:
        logger.info("Step 3/5  Date parsing skipped (no collection_date column).")

    if "geo_loc_name" in df.columns:
        logger.info("Step 4/5  Geospatial parsing")
        ge = GeoEngine()
        geo_df = ge.parse(df["geo_loc_name"])
        for col in geo_df.columns:
            df[col] = geo_df[col]
        resolved = df["geo_country"].notna().sum() if "geo_country" in df.columns else 0
        logger.info("         %d / %d geo_loc_name values resolved to country.", resolved, len(df))
    else:
        logger.info("Step 4/5  Geospatial parsing skipped (no geo_loc_name column).")

    logger.info("Step 5/5  One Health classification")
    classifier = OneHealthClassifier()
    src_cols = ["isolation_source", "env_broad_scale", "env_local_scale", "env_medium", "sample_type", "host"]
    present = {col: df[col] for col in src_cols if col in df.columns}

    if present:
        try:
            oh_df = classifier.classify_multi_field(**present)
            for col in oh_df.columns:
                df[col] = oh_df[col]
            logger.info("         %d source fields used.", len(present))
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
        classified = df["one_health_category"].notna().sum()
        logger.info("         %d / %d records classified.", classified, len(df))

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

    print(f"Done. {len(df)} records x {len(df.columns)} columns -> {output_path}", file=sys.stdout)
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        sys.exit(_run(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
