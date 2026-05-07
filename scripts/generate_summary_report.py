#!/usr/bin/env python3
"""
generate_summary_report.py

Generates an interactive, self-contained HTML report from a
BioMetaHarmonizer-style Excel, CSV, TSV, or Parquet file.

Usage
-----
  python scripts/generate_summary_report.py input.xlsx [output.html]

Dependencies
------------
  pandas>=1.5
  numpy
  plotly>=2.32  (CDN, no local install required at runtime)
  openpyxl      (for .xlsx input)
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

# Maximum rows serialised into the inline data table.
# Larger datasets are truncated to keep HTML file size manageable.
_TABLE_ROW_CAP = 5_000


# ── helpers ──────────────────────────────────────────────────────────────────

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
    for n in names:
        if n in df.columns:
            return df[n]
    return None


def value_counts_json(series) -> str:
    counts = series.dropna().value_counts()
    counts = counts[counts.index.str.strip() != ""]
    return json.dumps({"labels": counts.index.tolist()[:25], "values": counts.values.tolist()[:25]})


def completeness_json(df: pd.DataFrame) -> str:
    pct = (df.notna().sum() / len(df) * 100).round(1)
    pct = pct.sort_values(ascending=False)
    return json.dumps({"cols": pct.index.tolist(), "pct": pct.values.tolist()})


def timeline_json(df: pd.DataFrame) -> str:
    date_col = col(df, "collection_date")
    if date_col is None:
        return "null"
    parsed = pd.to_datetime(date_col, errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return "null"
    by_year = parsed.dt.year.value_counts().sort_index()
    return json.dumps({"years": [int(y) for y in by_year.index.tolist()],
                       "counts": by_year.values.tolist()})


def geo_json(df: pd.DataFrame) -> str:
    c = col(df, "geo_country")
    if c is None:
        c = col(df, "geo_loc_name")
    if c is None:
        return "null"
    counts = c.dropna().value_counts()
    counts = counts[counts.index.str.strip() != ""]
    return json.dumps({"countries": counts.index.tolist()[:30],
                       "counts": counts.values.tolist()[:30]})


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
    """
    Bin one_health_confidence (float 0-1) into four ranges so the chart
    is readable instead of plotting individual float values as categories.
    """
    c = col(df, "one_health_confidence")
    if c is None:
        return "null"
    numeric = pd.to_numeric(c, errors="coerce").dropna()
    if numeric.empty:
        return "null"
    bins = [0.0, 0.30, 0.60, 0.85, 1.001]
    labels = ["0.00-0.30", "0.30-0.60", "0.60-0.85", "0.85-1.00"]
    counts = pd.cut(numeric, bins=bins, labels=labels, right=False).value_counts().sort_index()
    return json.dumps({"labels": counts.index.tolist(), "values": counts.values.tolist()})


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


def submission_timeline_json(df: pd.DataFrame) -> str:
    c = col(df, "submission_date")
    if c is None:
        return "null"
    parsed = pd.to_datetime(c, errors="coerce").dropna()
    if parsed.empty:
        return "null"
    by_month = parsed.dt.to_period("M").value_counts().sort_index()
    return json.dumps({"months": [str(m) for m in by_month.index],
                       "counts": by_month.values.tolist()})


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
        "biosample_accession", "organism_name", "strain", "collection_date",
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

    if len(sub) > _TABLE_ROW_CAP:
        logger.warning(
            "Data table truncated to %d rows (dataset has %d). "
            "Increase _TABLE_ROW_CAP if needed.",
            _TABLE_ROW_CAP, len(sub),
        )
        sub = sub.iloc[:_TABLE_ROW_CAP]

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


# ── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BioSample Report</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --surface2: #22263a;
    --accent: #4f8ef7; --accent2: #7c5cbf; --accent3: #2ec4b6;
    --text: #e2e8f0; --muted: #8892a4; --border: #2a2f45;
    --green: #22c55e; --yellow: #f59e0b; --red: #ef4444;
    --card-radius: 12px; --font: 'Inter', system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         font-size: 14px; line-height: 1.6; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* Layout */
  .sidebar { position: fixed; left: 0; top: 0; bottom: 0; width: 220px;
             background: var(--surface); border-right: 1px solid var(--border);
             padding: 20px 0; overflow-y: auto; z-index: 100; }
  .sidebar-logo { padding: 0 20px 20px; border-bottom: 1px solid var(--border);
                  font-weight: 700; font-size: 16px; color: var(--accent); }
  .sidebar-logo span { display: block; font-size: 11px; color: var(--muted);
                        font-weight: 400; margin-top: 2px; }
  .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 20px;
              cursor: pointer; color: var(--muted); transition: all .2s;
              font-size: 13px; }
  .nav-item:hover, .nav-item.active { background: var(--surface2);
                                      color: var(--text); }
  .nav-item.active { border-right: 3px solid var(--accent); }
  .nav-icon { font-size: 16px; width: 20px; text-align: center; }

  .main { margin-left: 220px; padding: 30px; min-height: 100vh; }
  .page-header { margin-bottom: 28px; }
  .page-header h1 { font-size: 24px; font-weight: 700; }
  .page-header p { color: var(--muted); margin-top: 4px; font-size: 13px; }

  /* Sections */
  .section { display: none; }
  .section.active { display: block; }

  /* Stat cards */
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
               gap: 16px; margin-bottom: 28px; }
  .stat-card { background: var(--surface); border: 1px solid var(--border);
               border-radius: var(--card-radius); padding: 20px 16px; }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent); line-height: 1; }
  .stat-label { font-size: 12px; color: var(--muted); margin-top: 6px; }

  /* Chart cards */
  .chart-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
                gap: 20px; margin-bottom: 28px; }
  .chart-card { background: var(--surface); border: 1px solid var(--border);
                border-radius: var(--card-radius); padding: 20px; }
  .chart-card h3 { font-size: 14px; font-weight: 600; margin-bottom: 14px;
                   color: var(--text); }
  .chart-card.wide { grid-column: 1 / -1; }

  /* Table */
  .table-wrap { background: var(--surface); border: 1px solid var(--border);
                border-radius: var(--card-radius); overflow: hidden; }
  .table-toolbar { display: flex; align-items: center; gap: 12px; padding: 14px 16px;
                   border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .table-toolbar input { background: var(--surface2); border: 1px solid var(--border);
                          color: var(--text); padding: 7px 12px; border-radius: 8px;
                          font-size: 13px; flex: 1; min-width: 200px; outline: none; }
  .table-toolbar input:focus { border-color: var(--accent); }
  .table-toolbar select { background: var(--surface2); border: 1px solid var(--border);
                           color: var(--text); padding: 7px 10px; border-radius: 8px;
                           font-size: 13px; outline: none; cursor: pointer; }
  .table-count { font-size: 12px; color: var(--muted); margin-left: auto; }
  table { width: 100%; border-collapse: collapse; }
  thead th { background: var(--surface2); padding: 10px 14px; text-align: left;
             font-size: 12px; color: var(--muted); white-space: nowrap;
             border-bottom: 1px solid var(--border); cursor: pointer;
             user-select: none; position: sticky; top: 0; }
  thead th:hover { color: var(--text); }
  thead th .sort-icon { margin-left: 4px; opacity: 0.4; font-style: normal; }
  thead th.sort-asc .sort-icon::after { content: '▲'; opacity: 1; }
  thead th.sort-desc .sort-icon::after { content: '▼'; opacity: 1; }
  thead th:not(.sort-asc):not(.sort-desc) .sort-icon::after { content: '⇅'; }
  tbody tr { border-bottom: 1px solid var(--border); transition: background .15s; }
  tbody tr:hover { background: var(--surface2); }
  td { padding: 9px 14px; font-size: 13px; max-width: 280px;
       overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  td a { font-family: monospace; font-size: 12px; }
  .table-scroll { overflow-x: auto; max-height: 560px; overflow-y: auto; }
  .pagination { display: flex; align-items: center; gap: 8px; padding: 14px 16px;
                border-top: 1px solid var(--border); }
  .pag-btn { background: var(--surface2); border: 1px solid var(--border);
             color: var(--text); padding: 5px 12px; border-radius: 6px;
             font-size: 12px; cursor: pointer; }
  .pag-btn:disabled { opacity: 0.35; cursor: default; }
  .pag-btn:not(:disabled):hover { border-color: var(--accent); }
  .pag-info { font-size: 12px; color: var(--muted); margin-left: auto; }

  /* Completeness bar */
  .completeness-bar { height: 8px; background: var(--surface2); border-radius: 4px;
                      overflow: hidden; margin-top: 6px; }
  .completeness-bar div { height: 100%; border-radius: 4px;
                          background: linear-gradient(90deg, var(--accent), var(--accent3)); }

  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 99px;
           font-size: 11px; font-weight: 600; }
  .badge-green { background: rgba(34,197,94,.15); color: var(--green); }
  .badge-yellow { background: rgba(245,158,11,.15); color: var(--yellow); }
  .badge-red { background: rgba(239,68,68,.15); color: var(--red); }
  .badge-blue { background: rgba(79,142,247,.15); color: var(--accent); }

  /* Responsive */
  @media (max-width: 768px) {
    .sidebar { transform: translateX(-220px); }
    .main { margin-left: 0; padding: 16px; }
    .chart-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<nav class="sidebar">
  <div class="sidebar-logo">BioSample Report<span>BioMetaHarmonizer</span></div>
  <div class="nav-item active" onclick="showSection('overview')" id="nav-overview">
    <span class="nav-icon">📊</span> Overview
  </div>
  <div class="nav-item" onclick="showSection('taxonomy')" id="nav-taxonomy">
    <span class="nav-icon">🔬</span> Taxonomy
  </div>
  <div class="nav-item" onclick="showSection('geography')" id="nav-geography">
    <span class="nav-icon">🌍</span> Geography
  </div>
  <div class="nav-item" onclick="showSection('temporal')" id="nav-temporal">
    <span class="nav-icon">📅</span> Temporal
  </div>
  <div class="nav-item" onclick="showSection('onehealth')" id="nav-onehealth">
    <span class="nav-icon">🏥</span> One Health
  </div>
  <div class="nav-item" onclick="showSection('completeness')" id="nav-completeness">
    <span class="nav-icon">✅</span> Completeness
  </div>
  <div class="nav-item" onclick="showSection('table')" id="nav-table">
    <span class="nav-icon">📋</span> Data Table
  </div>
</nav>

<main class="main">

<!-- ── Overview ────────────────────────────────────────────────────────── -->
<div class="section active" id="section-overview">
  <div class="page-header">
    <h1>Dataset Overview</h1>
    <p>Generated: __GEN_DATE__ &nbsp;·&nbsp; Source: __SOURCE_FILE__</p>
  </div>
  <div class="stat-grid" id="stat-grid"></div>
  <div class="chart-grid">
    <div class="chart-card" id="chart-taxonomy-ov" style="min-height:320px">
      <h3>Organism Distribution</h3>
      <div id="fig-taxonomy-ov" style="height:280px"></div>
    </div>
    <div class="chart-card" id="chart-geo-ov" style="min-height:320px">
      <h3>Geographic Distribution</h3>
      <div id="fig-geo-ov" style="height:280px"></div>
    </div>
    <div class="chart-card" id="chart-sample-type" style="min-height:320px">
      <h3>Sample Type</h3>
      <div id="fig-sample-type" style="height:280px"></div>
    </div>
    <div class="chart-card" id="chart-access" style="min-height:320px">
      <h3>Access &amp; Status</h3>
      <div id="fig-access" style="height:280px"></div>
    </div>
    <div class="chart-card" id="chart-bioprojects-ov" style="min-height:320px">
      <h3>Top BioProjects (top 25)</h3>
      <div id="fig-bioprojects-ov" style="height:280px"></div>
    </div>
  </div>
</div>

<!-- ── Taxonomy ─────────────────────────────────────────────────────────── -->
<div class="section" id="section-taxonomy">
  <div class="page-header"><h1>Taxonomy</h1></div>
  <div class="chart-grid">
    <div class="chart-card wide" style="min-height:360px">
      <h3>Organism Names (top 25)</h3>
      <div id="fig-taxonomy-bar" style="height:320px"></div>
    </div>
    <div class="chart-card" style="min-height:360px">
      <h3>Host Distribution</h3>
      <div id="fig-host" style="height:320px"></div>
    </div>
    <div class="chart-card" style="min-height:360px">
      <h3>Host Disease</h3>
      <div id="fig-host-disease" style="height:320px"></div>
    </div>
    <div class="chart-card" style="min-height:360px">
      <h3>Isolation Source</h3>
      <div id="fig-isolation" style="height:320px"></div>
    </div>
  </div>
</div>

<!-- ── Geography ───────────────────────────────────────────────────────── -->
<div class="section" id="section-geography">
  <div class="page-header"><h1>Geography</h1></div>
  <div class="chart-grid">
    <div class="chart-card wide" style="min-height:420px">
      <h3>Samples by Country (top 30)</h3>
      <div id="fig-geo-bar" style="height:380px"></div>
    </div>
    <div class="chart-card wide" style="min-height:420px">
      <h3>World Map</h3>
      <div id="fig-geo-map" style="height:380px"></div>
    </div>
  </div>
</div>

<!-- ── Temporal ─────────────────────────────────────────────────────────── -->
<div class="section" id="section-temporal">
  <div class="page-header"><h1>Temporal</h1></div>
  <div class="chart-grid">
    <div class="chart-card wide" style="min-height:340px">
      <h3>Collection Date by Year</h3>
      <div id="fig-timeline" style="height:300px"></div>
    </div>
    <div class="chart-card wide" style="min-height:340px">
      <h3>Submission Date by Month</h3>
      <div id="fig-submission" style="height:300px"></div>
    </div>
  </div>
</div>

<!-- ── One Health ───────────────────────────────────────────────────────── -->
<div class="section" id="section-onehealth">
  <div class="page-header"><h1>One Health Annotation</h1></div>
  <div class="chart-grid">
    <div class="chart-card" style="min-height:340px">
      <h3>Category</h3>
      <div id="fig-oh-cat" style="height:300px"></div>
    </div>
    <div class="chart-card" style="min-height:340px">
      <h3>Confidence Score Distribution</h3>
      <div id="fig-oh-conf" style="height:300px"></div>
    </div>
    <div class="chart-card wide" style="min-height:340px">
      <h3>Evidence Level</h3>
      <div id="fig-oh-evid" style="height:300px"></div>
    </div>
  </div>
</div>

<!-- ── Completeness ─────────────────────────────────────────────────────── -->
<div class="section" id="section-completeness">
  <div class="page-header"><h1>Metadata Completeness</h1></div>
  <div class="chart-card wide" style="min-height:500px; margin-bottom:20px">
    <h3>Field Completeness (%)</h3>
    <div id="fig-completeness" style="height:460px"></div>
  </div>
</div>

<!-- ── Data Table ───────────────────────────────────────────────────────── -->
<div class="section" id="section-table">
  <div class="page-header"><h1>Data Table</h1></div>
  <div class="table-wrap">
    <div class="table-toolbar">
      <input type="text" id="table-search" placeholder="Search all columns…" oninput="filterTable()"/>
      <select id="col-filter" onchange="filterTable()"><option value="">All columns</option></select>
      <span class="table-count" id="table-count"></span>
    </div>
    <div class="table-scroll">
      <table id="data-table">
        <thead id="thead"></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
    <div class="pagination">
      <button class="pag-btn" id="btn-prev" onclick="changePage(-1)">← Prev</button>
      <button class="pag-btn" id="btn-next" onclick="changePage(1)">Next →</button>
      <span class="pag-info" id="pag-info"></span>
    </div>
  </div>
</div>

</main>

<script>
// ── Data injected by Python ───────────────────────────────────────────────
const STATS      = __STATS__;
const TAX_DATA   = __TAX_DATA__;
const GEO_DATA   = __GEO_DATA__;
const HOST_DATA  = __HOST_DATA__;
const TIMELINE   = __TIMELINE__;
const SUBMIT_TL  = __SUBMIT_TL__;
const OH_CAT     = __OH_CAT__;
const OH_CONF    = __OH_CONF__;
const OH_EVID    = __OH_EVID__;
const COMP_DATA  = __COMP_DATA__;
const STYPE_DATA = __STYPE_DATA__;
const ACCESS_DATA= __ACCESS_DATA__;
const STATUS_DATA= __STATUS_DATA__;
const HDISC_DATA = __HDISC_DATA__;
const ISOL_DATA  = __ISOL_DATA__;
const BPROJ_DATA = __BPROJ_DATA__;
const TABLE_DATA = __TABLE_DATA__;

// ── Plotly layout defaults ────────────────────────────────────────────────
const LAYOUT_BASE = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor:  'rgba(0,0,0,0)',
  font: { color: '#e2e8f0', family: 'Inter, system-ui, sans-serif', size: 12 },
  margin: { t: 10, b: 40, l: 10, r: 10 },
  colorway: ['#4f8ef7','#7c5cbf','#2ec4b6','#f59e0b','#ef4444',
             '#22c55e','#f97316','#a855f7','#14b8a6','#e879f9'],
};
const CFG = { responsive: true, displayModeBar: false };

function layout(extras) { return Object.assign({}, LAYOUT_BASE, extras); }

// ── Navigation ────────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  renderSection(name);
}

// ── Stat cards ────────────────────────────────────────────────────────────
function buildStats() {
  const items = [
    { label: 'Total Samples', value: STATS.total.toLocaleString() },
    { label: 'With Assembly', value: STATS.has_assembly.toLocaleString() },
    { label: 'With SRA',      value: STATS.has_sra.toLocaleString() },
    { label: 'Taxa',          value: STATS.n_taxa.toLocaleString() },
    { label: 'Countries',     value: STATS.n_countries.toLocaleString() },
    { label: 'BioProjects',   value: STATS.n_bioprojects.toLocaleString() },
    { label: 'Columns',       value: STATS.n_columns.toLocaleString() },
    { label: 'Completeness',  value: STATS.completeness + '%' },
  ];
  const grid = document.getElementById('stat-grid');
  items.forEach(i => {
    grid.insertAdjacentHTML('beforeend', `
      <div class="stat-card">
        <div class="stat-value">${i.value}</div>
        <div class="stat-label">${i.label}</div>
      </div>`);
  });
}

// ── Chart helpers ─────────────────────────────────────────────────────────
function barH(el, data, height) {
  if (!data) { document.getElementById(el).innerHTML = '<p style="color:var(--muted);padding:20px">No data</p>'; return; }
  Plotly.newPlot(el, [{
    type: 'bar', orientation: 'h',
    x: data.values, y: data.labels,
    marker: { color: '#4f8ef7', opacity: 0.85 },
    text: data.values.map(String), textposition: 'outside',
  }], layout({
    margin: { t: 10, b: 30, l: 160, r: 40 },
    yaxis: { automargin: true },
    xaxis: { showgrid: true, gridcolor: '#2a2f45' },
    height: height || 280,
  }), CFG);
}

function pie(el, data, height) {
  if (!data) { document.getElementById(el).innerHTML = '<p style="color:var(--muted);padding:20px">No data</p>'; return; }
  Plotly.newPlot(el, [{
    type: 'pie', labels: data.labels, values: data.values,
    hole: 0.42, textinfo: 'percent+label',
    textfont: { size: 11 },
  }], layout({
    margin: { t: 10, b: 10, l: 10, r: 10 },
    showlegend: false,
    height: height || 280,
  }), CFG);
}

function bar(el, labels, values, color, height) {
  Plotly.newPlot(el, [{
    type: 'bar', x: labels, y: values,
    marker: { color: color || '#4f8ef7', opacity: 0.85 },
  }], layout({
    margin: { t: 10, b: 60, l: 50, r: 10 },
    xaxis: { tickangle: -35 },
    yaxis: { showgrid: true, gridcolor: '#2a2f45' },
    height: height || 280,
  }), CFG);
}

// ── Section renderers ─────────────────────────────────────────────────────
const rendered = {};
function renderSection(name) {
  if (rendered[name]) return;
  rendered[name] = true;

  if (name === 'overview') {
    pie('fig-taxonomy-ov', TAX_DATA);
    pie('fig-geo-ov', GEO_DATA);
    pie('fig-sample-type', STYPE_DATA);
    const accessData = ACCESS_DATA && STATUS_DATA
      ? { labels: [...(ACCESS_DATA.labels||[]), ...(STATUS_DATA.labels||[])],
          values: [...(ACCESS_DATA.values||[]), ...(STATUS_DATA.values||[])] }
      : (ACCESS_DATA || STATUS_DATA);
    pie('fig-access', accessData);
    barH('fig-bioprojects-ov', BPROJ_DATA, 280);
  }

  if (name === 'taxonomy') {
    barH('fig-taxonomy-bar', TAX_DATA, 320);
    pie('fig-host', HOST_DATA);
    barH('fig-host-disease', HDISC_DATA);
    barH('fig-isolation', ISOL_DATA);
  }

  if (name === 'geography') {
    barH('fig-geo-bar', GEO_DATA, 380);
    if (GEO_DATA) {
      Plotly.newPlot('fig-geo-map', [{
        type: 'choropleth', locationmode: 'country names',
        locations: GEO_DATA.countries, z: GEO_DATA.counts,
        colorscale: [['0','#1a1d27'],['1','#4f8ef7']],
        showscale: true,
        colorbar: { bgcolor: 'rgba(0,0,0,0)', tickcolor: '#8892a4',
                    tickfont: { color: '#8892a4' } },
      }], layout({
        geo: { bgcolor: 'rgba(0,0,0,0)', landcolor: '#22263a',
               coastlinecolor: '#2a2f45', showcoastlines: true,
               showland: true, showocean: true, oceancolor: '#15182a',
               showframe: false },
        margin: { t: 0, b: 0, l: 0, r: 0 },
        height: 380,
      }), CFG);
    } else {
      document.getElementById('fig-geo-map').innerHTML = '<p style="color:var(--muted);padding:20px">No geo data</p>';
    }
  }

  if (name === 'temporal') {
    if (TIMELINE) {
      Plotly.newPlot('fig-timeline', [{
        type: 'bar', x: TIMELINE.years, y: TIMELINE.counts,
        marker: { color: '#2ec4b6', opacity: 0.85 },
      }], layout({
        xaxis: { title: 'Year', dtick: 1, tickangle: -45 },
        yaxis: { showgrid: true, gridcolor: '#2a2f45', title: 'Samples' },
        margin: { t: 10, b: 70, l: 55, r: 10 },
        height: 300,
      }), CFG);
    } else {
      document.getElementById('fig-timeline').innerHTML = '<p style="color:var(--muted);padding:20px">No collection date data</p>';
    }
    if (SUBMIT_TL) {
      Plotly.newPlot('fig-submission', [{
        type: 'scatter', mode: 'lines+markers',
        x: SUBMIT_TL.months, y: SUBMIT_TL.counts,
        line: { color: '#f59e0b', width: 2 },
        marker: { color: '#f59e0b', size: 5 },
        fill: 'tozeroy', fillcolor: 'rgba(245,158,11,0.08)',
      }], layout({
        xaxis: { tickangle: -45 },
        yaxis: { showgrid: true, gridcolor: '#2a2f45' },
        margin: { t: 10, b: 70, l: 50, r: 10 },
        height: 300,
      }), CFG);
    } else {
      document.getElementById('fig-submission').innerHTML = '<p style="color:var(--muted);padding:20px">No submission date data</p>';
    }
  }

  if (name === 'onehealth') {
    pie('fig-oh-cat', OH_CAT, 300);
    bar('fig-oh-conf', OH_CONF ? OH_CONF.labels : [], OH_CONF ? OH_CONF.values : [], '#7c5cbf', 300);
    bar('fig-oh-evid', OH_EVID ? OH_EVID.labels : [], OH_EVID ? OH_EVID.values : [], '#2ec4b6', 300);
  }

  if (name === 'completeness') {
    if (COMP_DATA) {
      const n = COMP_DATA.cols.length;
      const colors = COMP_DATA.pct.map(p =>
        p >= 80 ? '#22c55e' : p >= 40 ? '#f59e0b' : '#ef4444');
      Plotly.newPlot('fig-completeness', [{
        type: 'bar', orientation: 'h',
        x: COMP_DATA.pct, y: COMP_DATA.cols,
        marker: { color: colors },
        text: COMP_DATA.pct.map(p => p + '%'), textposition: 'outside',
      }], layout({
        margin: { t: 10, b: 30, l: 200, r: 60 },
        xaxis: { range: [0, 115], showgrid: true, gridcolor: '#2a2f45' },
        yaxis: { automargin: true },
        height: Math.max(460, n * 22),
      }), CFG);
    }
  }
}

// ── Data Table ────────────────────────────────────────────────────────────
let tblAll = [], tblFiltered = [];
let tblPage = 0, tblPageSize = 50;
let sortCol = -1, sortDir = 1;

function buildTable() {
  if (!TABLE_DATA) return;
  const cols = TABLE_DATA.columns;
  const rows = TABLE_DATA.rows;
  tblAll = rows;
  tblFiltered = rows;

  const thead = document.getElementById('thead');
  const tr = document.createElement('tr');
  cols.forEach((c, i) => {
    const th = document.createElement('th');
    th.innerHTML = c + ' <i class="sort-icon"></i>';
    th.onclick = () => sortTable(i);
    tr.appendChild(th);
  });
  thead.appendChild(tr);

  const sel = document.getElementById('col-filter');
  cols.forEach((c, i) => {
    const opt = document.createElement('option');
    opt.value = i; opt.textContent = c;
    sel.appendChild(opt);
  });

  renderTable();
}

const ACCESSION_RE = /^(SAM[DENA]\d+|GC[AF]_\d+\.\d+|SRR\d+|ERR\d+|PRJ[A-Z]+\d+|PRJDB\d+)$/;
const NCBI_BASES = {
  'SAM': 'https://www.ncbi.nlm.nih.gov/biosample/',
  'GCF': 'https://www.ncbi.nlm.nih.gov/datasets/genome/',
  'GCA': 'https://www.ncbi.nlm.nih.gov/datasets/genome/',
  'SRR': 'https://www.ncbi.nlm.nih.gov/sra/',
  'ERR': 'https://www.ebi.ac.uk/ena/browser/view/',
  'PRJ': 'https://www.ncbi.nlm.nih.gov/bioproject/',
};

function makeLink(v) {
  if (!v || v === '') return '';
  if (ACCESSION_RE.test(v)) {
    const prefix = v.slice(0, 3);
    const base = NCBI_BASES[prefix] || NCBI_BASES[v.slice(0,3)];
    if (base) return `<a href="${base}${v}" target="_blank">${v}</a>`;
  }
  return escHtml(v);
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderTable() {
  const cols = TABLE_DATA.columns;
  const start = tblPage * tblPageSize;
  const slice = tblFiltered.slice(start, start + tblPageSize);
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';
  slice.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      td.innerHTML = makeLink(cell == null ? '' : String(cell));
      td.title = cell == null ? '' : String(cell);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  document.getElementById('table-count').textContent =
    tblFiltered.length.toLocaleString() + ' rows';
  document.getElementById('pag-info').textContent =
    `Page ${tblPage + 1} / ${Math.max(1, Math.ceil(tblFiltered.length / tblPageSize))}`;
  document.getElementById('btn-prev').disabled = tblPage === 0;
  document.getElementById('btn-next').disabled =
    (tblPage + 1) * tblPageSize >= tblFiltered.length;
}

function filterTable() {
  const q = document.getElementById('table-search').value.toLowerCase();
  const colIdx = document.getElementById('col-filter').value;
  tblFiltered = tblAll.filter(row => {
    if (!q) return true;
    if (colIdx !== '') {
      const v = row[+colIdx];
      return v != null && String(v).toLowerCase().includes(q);
    }
    return row.some(v => v != null && String(v).toLowerCase().includes(q));
  });
  tblPage = 0;
  renderTable();
}

function changePage(dir) {
  tblPage += dir;
  renderTable();
}

function sortTable(idx) {
  const ths = document.querySelectorAll('#thead th');
  ths.forEach((th, i) => {
    th.classList.remove('sort-asc', 'sort-desc');
  });
  if (sortCol === idx) { sortDir *= -1; }
  else { sortCol = idx; sortDir = 1; }
  ths[idx].classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
  tblFiltered.sort((a, b) => {
    const av = a[idx] ?? '';
    const bv = b[idx] ?? '';
    if (!isNaN(av) && !isNaN(bv) && av !== '' && bv !== '')
      return (Number(av) - Number(bv)) * sortDir;
    return String(av).localeCompare(String(bv)) * sortDir;
  });
  tblPage = 0;
  renderTable();
}

// ── Init ──────────────────────────────────────────────────────────────────
buildStats();
renderSection('overview');
buildTable();
</script>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_report(input_path: str, output_path: str | None = None) -> str:
    df = load_data(input_path)

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
    html = html.replace("__TIMELINE__",    timeline_json(df))
    html = html.replace("__SUBMIT_TL__",   submission_timeline_json(df))
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


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML report from a BioMetaHarmonizer Excel/CSV/TSV/Parquet file.")
    parser.add_argument("input", help="Input .xlsx, .csv, .tsv, or .parquet file")
    parser.add_argument("output", nargs="?", default=None,
                        help="Output HTML file (default: <input>_report.html)")
    args = parser.parse_args()
    generate_report(args.input, args.output)


if __name__ == "__main__":
    main()
