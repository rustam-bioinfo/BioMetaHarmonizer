#!/usr/bin/env python3
"""
generate_summary_report.py

Generates an interactive, self-contained HTML report from a
BioMetaHarmonizer output file (CSV, TSV, Excel, or Parquet).

Usage
-----
  # Via the CLI entry-point (recommended):
  biometaharmonizer generate-report harmonized.csv
  biometaharmonizer generate-report harmonized.csv report.html

  # As a standalone script (backwards-compatible):
  python -m biometaharmonizer.scripts.generate_summary_report harmonized.csv

Dependencies
------------
  pandas>=1.5
  numpy
  plotly>=2.32  (loaded from CDN; no local install required at runtime)
  openpyxl      (for .xlsx / .xls input)
  pyarrow       (for .parquet input, optional)
"""

import sys
import os
import json
import re
import argparse
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_CHART_TOP_N = 25
_PIE_TOP_N = 10


def safe_val(v):
    if pd.isna(v) or v is None or str(v).strip() in ("", "nan", "NaN", "None"):
        return None
    return v


def load_data(path: str) -> pd.DataFrame:
    ext = Path(path).suffix.lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(path, dtype=str)
    elif ext == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif ext == ".tsv":
        df = pd.read_csv(path, sep="\t", dtype=str)
    elif ext == ".parquet":
        df = pd.read_parquet(path).astype(str).replace("nan", None)
    else:
        raise ValueError(f"Unsupported file format: {ext}")
    df = df.where(df.notna(), None)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def col(df: pd.DataFrame, *names) -> pd.Series | None:
    for i, n in enumerate(names):
        if n in df.columns:
            if i > 0:
                logger.debug("Column '%s' not found; falling back to '%s'.", names[0], n)
            return df[n]
    return None


def value_counts_json(series, top_n: int = _CHART_TOP_N) -> str:
    counts = series.dropna().value_counts()
    counts = counts[counts.index.str.strip() != ""]
    total_unique = len(counts)
    capped = total_unique > top_n
    counts = counts.iloc[:top_n]
    return json.dumps({
        "labels": counts.index.tolist(),
        "values": counts.values.tolist(),
        "capped": capped,
        "top_n": top_n,
    })


def completeness_json(df: pd.DataFrame) -> str:
    pct = (df.notna().sum() / len(df) * 100).round(1)
    pct = pct.sort_values(ascending=False)
    return json.dumps({"cols": pct.index.tolist(), "pct": pct.values.tolist()})


def timeline_json(df: pd.DataFrame, total: int) -> str:
    date_col = col(df, "collection_date")
    if date_col is None:
        return "null"
    parsed = pd.to_datetime(date_col, errors="coerce")
    missing = int(parsed.isna().sum())
    parsed = parsed.dropna()
    if parsed.empty:
        return "null"
    by_year = parsed.dt.year.value_counts().sort_index()
    return json.dumps({
        "years":   [int(y) for y in by_year.index.tolist()],
        "counts":  by_year.values.tolist(),
        "missing": missing,
        "total":   total,
    })


def submission_timeline_json(df: pd.DataFrame, total: int) -> str:
    c = col(df, "submission_date")
    if c is None:
        return "null"
    parsed = pd.to_datetime(c, errors="coerce")
    missing = int(parsed.isna().sum())
    parsed = parsed.dropna()
    if parsed.empty:
        return "null"
    by_year = parsed.dt.year.value_counts().sort_index()
    return json.dumps({
        "years":   [int(y) for y in by_year.index.tolist()],
        "counts":  by_year.values.tolist(),
        "missing": missing,
        "total":   total,
    })


def geo_json(df: pd.DataFrame) -> str:
    c = col(df, "geo_country", "geo_loc_name")
    if c is None:
        return "null"
    counts = c.dropna().value_counts()
    counts = counts[counts.index.str.strip() != ""]
    total = len(counts)
    capped = total > 30
    counts = counts.iloc[:30]
    labels = counts.index.tolist()
    values = counts.values.tolist()
    return json.dumps({
        "labels":    labels,
        "values":    values,
        "countries": labels,
        "counts":    values,
        "capped":    capped,
        "top_n":     30,
    })


def host_json(df: pd.DataFrame) -> str:
    c = col(df, "host")
    if c is None:
        return "null"
    return value_counts_json(c)


def taxonomy_json(df: pd.DataFrame) -> str:
    c = col(df, "organism_name", "taxonomy_name")
    if c is None:
        return "null"
    return value_counts_json(c)


def oh_category_json(df: pd.DataFrame) -> str:
    c = col(df, "one_health_category")
    if c is None:
        return "null"
    return value_counts_json(c)


def confidence_json(df: pd.DataFrame) -> str:
    c = col(df, "one_health_confidence")
    if c is None:
        return "null"
    numeric = pd.to_numeric(c, errors="coerce").dropna()
    if numeric.empty:
        return "null"
    bins = [0.0, 0.30, 0.60, 0.85, 1.001]
    labels = ["0.00-0.30", "0.30-0.60", "0.60-0.85", "0.85-1.00"]
    counts = pd.cut(numeric, bins=bins, labels=labels, right=False).value_counts().sort_index()
    return json.dumps({"labels": counts.index.tolist(), "values": counts.values.tolist(), "capped": False})


def evidence_json(df: pd.DataFrame) -> str:
    c = col(df, "one_health_evidence_level")
    if c is None:
        return "null"
    return value_counts_json(c)


def bioproject_json(df: pd.DataFrame) -> str:
    c = col(df, "bioproject_accession")
    if c is None:
        return "null"
    return value_counts_json(c)


def sample_type_json(df: pd.DataFrame) -> str:
    c = col(df, "sample_type")
    if c is None:
        return "null"
    return value_counts_json(c)


def access_json(df: pd.DataFrame) -> str:
    c = col(df, "access")
    if c is None:
        return "null"
    return value_counts_json(c)


def status_json(df: pd.DataFrame) -> str:
    c = col(df, "status")
    if c is None:
        return "null"
    return value_counts_json(c)


def host_disease_json(df: pd.DataFrame) -> str:
    c = col(df, "host_disease")
    if c is None:
        return "null"
    vals = c.dropna()
    vals = vals[vals.str.strip() != ""]
    if vals.empty:
        return "null"
    return value_counts_json(vals)


def isolation_source_json(df: pd.DataFrame) -> str:
    c = col(df, "isolation_source")
    if c is None:
        return "null"
    vals = c.dropna()
    vals = vals[vals.str.strip() != ""]
    if vals.empty:
        return "null"
    return value_counts_json(vals)


def df_to_records(df: pd.DataFrame) -> str:
    display_cols = [
        "biosample_accession", "organism_name", "strain",
        "collection_date", "collection_date_range",
        "geo_country", "host", "host_disease", "isolation_source",
        "assembly_accession_refseq", "assembly_accession_genbank",
        "bioproject_accession", "sra_accession", "one_health_category",
        "one_health_confidence", "one_health_evidence_level",
        "status", "access", "submission_date",
    ]
    available = [c for c in display_cols if c in df.columns]
    remaining = [c for c in df.columns if c not in available and c != "_extra_attributes"]
    final_cols = available + remaining
    sub = df[final_cols].fillna("")
    return json.dumps({"columns": final_cols, "rows": sub.values.tolist()})


def kv_stats(df: pd.DataFrame) -> dict:
    total = len(df)

    c_ref = col(df, "assembly_accession_refseq")
    c_gb  = col(df, "assembly_accession_genbank")
    has_ref = (c_ref.notna() & (c_ref.str.strip() != "")) if c_ref is not None else pd.Series(False, index=df.index)
    has_gb  = (c_gb.notna()  & (c_gb.str.strip()  != "")) if c_gb  is not None else pd.Series(False, index=df.index)
    has_assembly = int((has_ref | has_gb).sum())

    has_sra = 0
    c_sra = col(df, "sra_accession")
    if c_sra is not None:
        has_sra = int((c_sra.notna() & (c_sra.str.strip() != "")).sum())

    n_taxa = 0
    c_tax = col(df, "organism_name", "taxonomy_name")
    if c_tax is not None:
        n_taxa = c_tax.dropna().nunique()

    n_countries = 0
    c_geo = col(df, "geo_country", "geo_loc_name")
    if c_geo is not None:
        n_countries = c_geo.dropna().nunique()

    n_bioprojects = 0
    c_bp = col(df, "bioproject_accession")
    if c_bp is not None:
        n_bioprojects = c_bp.dropna().nunique()

    completeness = round(df.notna().values.sum() / (df.shape[0] * df.shape[1]) * 100, 1)

    return {
        "total":         total,
        "has_assembly":  has_assembly,
        "has_sra":       has_sra,
        "n_taxa":        int(n_taxa),
        "n_countries":   int(n_countries),
        "n_bioprojects": int(n_bioprojects),
        "n_columns":     len(df.columns),
        "completeness":  completeness,
    }


# ── HTML template — kept verbatim from original ───────────────────────────
# (import from original file at runtime to avoid duplication)
# The full template string is defined in generate_report() below.


def generate_report(input_path: str, output_path: str | None = None) -> str:
    # Import the HTML template from the original top-level script if it
    # exists (backwards compat), otherwise use the inline copy.
    try:
        import importlib.util, sys as _sys
        _spec = importlib.util.spec_from_file_location(
            "_gsr_orig",
            Path(__file__).resolve().parent.parent.parent.parent
            / "scripts" / "generate_summary_report.py",
        )
        if _spec and _spec.origin and Path(_spec.origin).exists():
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod.generate_report(input_path, output_path)
    except Exception:
        pass

    # Inline fallback — delegates to the helper functions defined above.
    from biometaharmonizer.scripts._report_template import HTML_TEMPLATE  # noqa: F401
    df = load_data(input_path)
    total = len(df)

    if output_path is None:
        stem = Path(input_path).stem
        output_path = str(Path(input_path).parent / f"{stem}_report.html")

    stats = kv_stats(df)

    html = HTML_TEMPLATE
    html = html.replace("__GEN_DATE__",    datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("__SOURCE_FILE__", Path(input_path).name)
    html = html.replace("__STATS__",       json.dumps(stats))
    html = html.replace("__TAX_DATA__",    taxonomy_json(df))
    html = html.replace("__GEO_DATA__",    geo_json(df))
    html = html.replace("__HOST_DATA__",   host_json(df))
    html = html.replace("__TIMELINE__",    timeline_json(df, total))
    html = html.replace("__SUBMIT_TL__",   submission_timeline_json(df, total))
    html = html.replace("__OH_CAT__",      oh_category_json(df))
    html = html.replace("__OH_CONF__",     confidence_json(df))
    html = html.replace("__OH_EVID__",     evidence_json(df))
    html = html.replace("__COMP_DATA__",   completeness_json(df))
    html = html.replace("__STYPE_DATA__",  sample_type_json(df))
    html = html.replace("__ACCESS_DATA__", access_json(df))
    html = html.replace("__STATUS_DATA__", status_json(df))
    html = html.replace("__HDISC_DATA__",  host_disease_json(df))
    html = html.replace("__ISOL_DATA__",   isolation_source_json(df))
    html = html.replace("__BPROJ_DATA__",  bioproject_json(df))
    html = html.replace("__TABLE_DATA__",  df_to_records(df))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Report written to: %s", output_path)
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML report from a BioMetaHarmonizer output file."
    )
    parser.add_argument("input", help="Input .xlsx, .csv, .tsv, or .parquet file")
    parser.add_argument("output", nargs="?", default=None,
                        help="Output HTML file (default: <input>_report.html)")
    args = parser.parse_args(argv)
    generate_report(args.input, args.output)


if __name__ == "__main__":
    main()
