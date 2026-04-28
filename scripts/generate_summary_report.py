#!/usr/bin/env python3
"""
generate_summary_report.py

Generates a comprehensive HTML summary report with visualizations for
BioMetaHarmonizer output files.

Usage
-----
  python scripts/generate_summary_report.py \
      --input harmonized.csv \
      --output report.html \
      --format html

  # Generate all formats:
  python scripts/generate_summary_report.py \
      --input harmonized.csv \
      --output-dir reports/ \
      --formats html pdf json

Dependencies
------------
  pandas>=1.5
  plotly>=5.14
  kaleido (for PDF export, optional)
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Import plotly components conditionally
try:
    import plotly.express as px
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    # Define stub types for type hints when plotly is not available
    class go:
        Figure = None
        Bar = None
        Pie = None
        Scatter = None
        Histogram = None
        Donut = None
    
    class px:
        colors = type('colors', (), {'qualitative': type('qualitative', (), {'Set3': []})})()
    
    def make_subplots(*args, **kwargs):
        raise ImportError("Plotly is required for visualization features")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Column categories for grouped analysis
COLUMN_CATEGORIES = {
    "Structural": [
        "biosample_accession", "biosample_id", "sra_accession", 
        "bioproject_accession", "assembly_accession_refseq", 
        "assembly_accession_genbank", "sample_name_id", 
        "taxonomy_id", "taxonomy_name"
    ],
    "Organism": ["organism_name"],
    "Temporal": ["collection_date", "collection_date_range"],
    "Geospatial": [
        "geo_loc_name", "geo_country", "geo_region", "geo_locality",
        "geo_iso3166", "geo_sea_ocean", "geo_loc_raw"
    ],
    "Host Information": [
        "host", "host_disease", "host_age", "host_sex", 
        "host_tissue_sampled", "host_health_state", "host_subject_id",
        "host_description", "host_disease_outcome", "host_disease_stage"
    ],
    "Sample/Isolation": ["isolation_source", "sample_type", "isolate"],
    "One Health": ["one_health_category"],
    "Strain/Typing": [
        "strain", "sub_strain", "serotype", "serovar", "genotype",
        "subtype", "subgroup", "pathotype", "passage_history"
    ],
    "Culture/Reference": [
        "culture_collection", "specimen_voucher", 
        "biomaterial_provider", "ref_biomaterial"
    ],
    "AMR": [
        "antimicrobial_resistance", 
        "antimicrobial_resistance_phenotype"
    ],
    "Epidemiology": ["outbreak"],
    "Sequencing/Assembly": ["sequencing_method", "assembly_method"],
    "Environmental": [
        "env_broad_scale", "env_local_scale", "env_medium",
        "samp_size", "samp_mat_process", "temp", "ph", "depth", "elev"
    ],
    "Curation": [
        "collected_by", "ncbi_package", "submission_date", 
        "last_update", "publication_date", "access", "status", 
        "status_date", "title", "description_comment"
    ],
    "Special Categories": ["ifsac_category", "food_origin"],
    "Extra": ["_extra_attributes"]
}

# Reverse mapping: column -> category
COLUMN_TO_CATEGORY = {}
for cat, cols in COLUMN_CATEGORIES.items():
    for col in cols:
        COLUMN_TO_CATEGORY[col] = cat


def compute_fill_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Compute fill rates for all columns."""
    n = len(df)
    rows = []
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        fill_pct = round(non_null / n * 100, 2) if n > 0 else 0.0
        category = COLUMN_TO_CATEGORY.get(col, "Uncategorized")
        rows.append({
            "column_name": col,
            "non_null_count": non_null,
            "null_count": n - non_null,
            "fill_pct": fill_pct,
            "category": category
        })
    return pd.DataFrame(rows)


def generate_quality_dashboard(df: pd.DataFrame, fill_df: pd.DataFrame) -> go.Figure:
    """Generate overall data quality dashboard."""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "Column Fill Rates by Category",
            "Overall Completeness Distribution",
            "Category-wise Average Fill Rate",
            "Top 15 Most Complete Columns"
        ),
        specs=[
            [{"type": "bar"}, {"type": "histogram"}],
            [{"type": "bar"}, {"type": "bar"}]
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.10
    )
    
    # Color scale for fill rates
    colors = []
    for pct in fill_df["fill_pct"]:
        if pct >= 80:
            colors.append("#2ecc71")  # Green
        elif pct >= 50:
            colors.append("#f39c12")  # Yellow
        else:
            colors.append("#e74c3c")  # Red
    
    # Plot 1: All columns fill rate
    fig.add_trace(
        go.Bar(
            x=fill_df["column_name"],
            y=fill_df["fill_pct"],
            marker_color=colors,
            name="Fill Rate",
            hovertemplate="<b>%{x}</b><br>Fill: %{y:.1f}%<extra></extra>"
        ),
        row=1, col=1
    )
    
    # Plot 2: Histogram of fill rates
    fig.add_trace(
        go.Histogram(
            x=fill_df["fill_pct"],
            nbinsx=20,
            name="Distribution",
            marker_color="#3498db",
            opacity=0.7
        ),
        row=1, col=2
    )
    
    # Plot 3: Category-wise average
    cat_avg = fill_df.groupby("category")["fill_pct"].mean().sort_values(ascending=True)
    fig.add_trace(
        go.Bar(
            x=cat_avg.values,
            y=cat_avg.index,
            orientation="h",
            name="Category Avg",
            marker_color="#9b59b6"
        ),
        row=2, col=1
    )
    
    # Plot 4: Top 15 columns
    top15 = fill_df.nlargest(15, "fill_pct")
    fig.add_trace(
        go.Bar(
            x=top15["fill_pct"],
            y=top15["column_name"],
            orientation="h",
            name="Top 15",
            marker_color="#1abc9c"
        ),
        row=2, col=2
    )
    
    fig.update_layout(
        height=800,
        showlegend=False,
        title_text="Data Quality Dashboard",
        title_font_size=20,
        template="plotly_white"
    )
    
    fig.update_xaxes(title_text="Fill Rate (%)", row=1, col=1)
    fig.update_xaxes(title_text="Fill Rate (%)", row=1, col=2)
    fig.update_xaxes(title_text="Avg Fill Rate (%)", row=2, col=1)
    fig.update_xaxes(title_text="Fill Rate (%)", row=2, col=2)
    
    return fig


def generate_geo_visualizations(df: pd.DataFrame) -> go.Figure:
    """Generate geographic distribution visualizations."""
    if "geo_country" not in df.columns:
        logger.warning("No geo_country column found; skipping geo visualizations.")
        return None
    
    country_counts = df["geo_country"].value_counts().reset_index()
    country_counts.columns = ["country", "count"]
    country_counts = country_counts.head(20)  # Top 20
    
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Top 20 Countries", "Geographic Resolution Success"),
        specs=[[{"type": "bar"}, {"type": "pie"}]]
    )
    
    # Bar chart
    fig.add_trace(
        go.Bar(
            x=country_counts["count"],
            y=country_counts["country"],
            orientation="h",
            marker_color="#3498db",
            name="Countries"
        ),
        row=1, col=1
    )
    
    # Pie chart for resolution success
    resolved = df["geo_country"].notna().sum()
    unresolved = len(df) - resolved
    
    fig.add_trace(
        go.Pie(
            labels=["Resolved", "Unresolved"],
            values=[resolved, unresolved],
            marker_colors=["#2ecc71", "#e74c3c"],
            name="Resolution"
        ),
        row=1, col=2
    )
    
    fig.update_layout(
        height=500,
        showlegend=False,
        title_text="Geographic Distribution",
        title_font_size=18,
        template="plotly_white"
    )
    
    return fig


def generate_temporal_analysis(df: pd.DataFrame) -> go.Figure:
    """Generate temporal analysis visualizations."""
    if "collection_date" not in df.columns:
        logger.warning("No collection_date column found; skipping temporal analysis.")
        return None
    
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Collection Timeline", "Date Parsing Success"),
        specs=[[{"type": "scatter"}], [{"type": "pie"}]]
    )
    
    # Parse dates for timeline
    date_col = df["collection_date"].dropna()
    if len(date_col) > 0:
        # Try to extract year for grouping
        years = []
        for val in date_col:
            try:
                if isinstance(val, str) and len(val) >= 4:
                    years.append(int(val[:4]))
                else:
                    years.append(None)
            except (ValueError, TypeError):
                years.append(None)
        
        year_counts = pd.Series(years).value_counts().sort_index()
        
        fig.add_trace(
            go.Scatter(
                x=year_counts.index.astype(str),
                y=year_counts.values,
                mode="lines+markers",
                marker_color="#e74c3c",
                name="Timeline"
            ),
            row=1, col=1
        )
    
    # Pie chart for parsing success
    parsed = df["collection_date"].notna().sum()
    unparsed = len(df) - parsed
    
    has_range = "collection_date_range" in df.columns
    ranges = df["collection_date_range"].notna().sum() if has_range else 0
    
    labels = ["Parsed (ISO)", "Ranges", "Missing"]
    values = [parsed - ranges, ranges, unparsed]
    
    fig.add_trace(
        go.Pie(
            labels=labels,
            values=values,
            marker_colors=["#2ecc71", "#f39c12", "#e74c3c"],
            name="Parsing Status"
        ),
        row=2, col=1
    )
    
    fig.update_layout(
        height=700,
        showlegend=False,
        title_text="Temporal Analysis",
        title_font_size=18,
        template="plotly_white"
    )
    
    return fig


def generate_one_health_chart(df: pd.DataFrame) -> go.Figure:
    """Generate One Health classification breakdown."""
    if "one_health_category" not in df.columns:
        logger.warning("No one_health_category column found; skipping One Health chart.")
        return None
    
    oh_counts = df["one_health_category"].value_counts().reset_index()
    oh_counts.columns = ["category", "count"]
    
    fig = go.Figure()
    
    fig.add_trace(
        go.Pie(
            labels=oh_counts["category"],
            values=oh_counts["count"],
            marker_colors=px.colors.qualitative.Set3,
            hole=0.4,
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>Count: %{value}<br>Percent: %{percent}<extra></extra>"
        )
    )
    
    fig.update_layout(
        height=600,
        title_text="One Health Classification Breakdown",
        title_font_size=18,
        template="plotly_white",
        showlegend=True,
        legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.05)
    )
    
    return fig


def generate_host_analysis(df: pd.DataFrame) -> go.Figure:
    """Generate host species analysis."""
    if "host" not in df.columns:
        logger.warning("No host column found; skipping host analysis.")
        return None
    
    host_counts = df["host"].value_counts().head(15).reset_index()
    host_counts.columns = ["host", "count"]
    
    fig = go.Figure()
    
    fig.add_trace(
        go.Bar(
            x=host_counts["count"],
            y=host_counts["host"],
            orientation="h",
            marker_color="#9b59b6",
            hovertemplate="<b>%{y}</b><br>Count: %{x}<extra></extra>"
        )
    )
    
    fig.update_layout(
        height=500,
        title_text="Top 15 Host Species",
        title_font_size=18,
        xaxis_title="Count",
        yaxis_title="Host",
        template="plotly_white",
        showlegend=False
    )
    
    return fig


def generate_extra_attributes_analysis(df: pd.DataFrame) -> Dict:
    """Analyze _extra_attributes column for unmapped fields."""
    if "_extra_attributes" not in df.columns:
        return {"summary": "No _extra_attributes column found"}
    
    extra_col = df["_extra_attributes"].dropna()
    if len(extra_col) == 0:
        return {"summary": "All attributes mapped; no extra attributes"}
    
    # Parse JSON strings and count keys
    all_keys = []
    for val in extra_col:
        try:
            if isinstance(val, str):
                data = json.loads(val)
                if isinstance(data, dict):
                    all_keys.extend(data.keys())
        except (json.JSONDecodeError, TypeError):
            continue
    
    key_counts = pd.Series(all_keys).value_counts().head(20)
    
    return {
        "total_records_with_extra": len(extra_col),
        "unique_unmapped_keys": len(set(all_keys)),
        "top_20_unmapped": key_counts.to_dict()
    }


def generate_full_html_report(
    df: pd.DataFrame,
    fill_df: pd.DataFrame,
    output_path: Path
) -> Path:
    """Generate complete HTML report with all visualizations."""
    if not PLOTLY_AVAILABLE:
        raise ImportError("Plotly is required for HTML report generation. Install with: pip install plotly")
    
    sections = []
    
    # Header
    sections.append(f"""
    <div style="font-family: Arial, sans-serif; padding: 20px;">
        <h1 style="color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px;">
            BioMetaHarmonizer Summary Report
        </h1>
        <p style="color: #7f8c8d;">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    """)
    
    # Executive Summary
    total_records = len(df)
    total_columns = len(df.columns)
    avg_fill = fill_df["fill_pct"].mean()
    high_quality_cols = (fill_df["fill_pct"] >= 80).sum()
    medium_quality_cols = ((fill_df["fill_pct"] >= 50) & (fill_df["fill_pct"] < 80)).sum()
    low_quality_cols = (fill_df["fill_pct"] < 50).sum()
    
    sections.append(f"""
    <div style="background-color: #ecf0f1; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <h2 style="color: #2c3e50;">Executive Summary</h2>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #3498db; margin: 0;">{total_records:,}</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">Total Records</p>
            </div>
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #3498db; margin: 0;">{total_columns}</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">Total Columns</p>
            </div>
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #3498db; margin: 0;">{avg_fill:.1f}%</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">Average Fill Rate</p>
            </div>
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #2ecc71; margin: 0;">{high_quality_cols}</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">High Quality (>80%)</p>
            </div>
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #f39c12; margin: 0;">{medium_quality_cols}</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">Medium Quality (50-80%)</p>
            </div>
            <div style="background: white; padding: 15px; border-radius: 5px; text-align: center;">
                <h3 style="color: #e74c3c; margin: 0;">{low_quality_cols}</h3>
                <p style="color: #7f8c8d; margin: 5px 0 0 0;">Low Quality (<50%)</p>
            </div>
        </div>
    </div>
    """)
    
    # Data Quality Dashboard
    quality_fig = generate_quality_dashboard(df, fill_df)
    sections.append(f"""
    <div style="margin: 30px 0;">
        <h2 style="color: #2c3e50;">Data Quality Dashboard</h2>
        {quality_fig.to_html(full_html=False, include_plotlyjs='cdn', default_height='800px')}
    </div>
    """)
    
    # Geographic Distribution
    geo_fig = generate_geo_visualizations(df)
    if geo_fig:
        sections.append(f"""
        <div style="margin: 30px 0;">
            <h2 style="color: #2c3e50;">Geographic Distribution</h2>
            {geo_fig.to_html(full_html=False, include_plotlyjs='cdn', default_height='500px')}
        </div>
        """)
    
    # Temporal Analysis
    temporal_fig = generate_temporal_analysis(df)
    if temporal_fig:
        sections.append(f"""
        <div style="margin: 30px 0;">
            <h2 style="color: #2c3e50;">Temporal Analysis</h2>
            {temporal_fig.to_html(full_html=False, include_plotlyjs='cdn', default_height='700px')}
        </div>
        """)
    
    # One Health Classification
    oh_fig = generate_one_health_chart(df)
    if oh_fig:
        sections.append(f"""
        <div style="margin: 30px 0;">
            <h2 style="color: #2c3e50;">One Health Classification</h2>
            {oh_fig.to_html(full_html=False, include_plotlyjs='cdn', default_height='600px')}
        </div>
        """)
    
    # Host Analysis
    host_fig = generate_host_analysis(df)
    if host_fig:
        sections.append(f"""
        <div style="margin: 30px 0;">
            <h2 style="color: #2c3e50;">Host Species Analysis</h2>
            {host_fig.to_html(full_html=False, include_plotlyjs='cdn', default_height='500px')}
        </div>
        """)
    
    # Extra Attributes Analysis
    extra_analysis = generate_extra_attributes_analysis(df)
    sections.append(f"""
    <div style="background-color: #f9f9f9; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <h2 style="color: #2c3e50;">Unmapped Attributes Analysis</h2>
        <p><strong>Total records with extra attributes:</strong> {extra_analysis.get('total_records_with_extra', 'N/A')}</p>
        <p><strong>Unique unmapped keys:</strong> {extra_analysis.get('unique_unmapped_keys', 'N/A')}</p>
    """)
    
    if "top_20_unmapped" in extra_analysis and extra_analysis["top_20_unmapped"]:
        sections.append("""
        <h3>Top 20 Unmapped Attributes:</h3>
        <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
            <tr style="background-color: #3498db; color: white;">
                <th style="padding: 10px; text-align: left;">Attribute Name</th>
                <th style="padding: 10px;">Frequency</th>
            </tr>
        """)
        for attr, count in list(extra_analysis["top_20_unmapped"].items())[:20]:
            sections.append(f"""
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 10px;">{attr}</td>
                <td style="padding: 10px; text-align: center;">{count}</td>
            </tr>
            """)
        sections.append("</table>")
    
    sections.append("</div>")
    
    # Fill Rate Table
    sections.append("""
    <div style="margin: 30px 0;">
        <h2 style="color: #2c3e50;">Complete Fill Rate Table</h2>
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <tr style="background-color: #3498db; color: white;">
                <th style="padding: 10px; text-align: left;">Column Name</th>
                <th style="padding: 10px;">Category</th>
                <th style="padding: 10px; text-align: right;">Non-Null</th>
                <th style="padding: 10px; text-align: right;">Null</th>
                <th style="padding: 10px; text-align: right;">Fill %</th>
            </tr>
    """)
    
    for _, row in fill_df.sort_values("fill_pct", ascending=False).iterrows():
        color = "#2ecc71" if row["fill_pct"] >= 80 else ("#f39c12" if row["fill_pct"] >= 50 else "#e74c3c")
        sections.append(f"""
        <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px;">{row['column_name']}</td>
            <td style="padding: 8px; color: #7f8c8d;">{row['category']}</td>
            <td style="padding: 8px; text-align: right;">{row['non_null_count']:,}</td>
            <td style="padding: 8px; text-align: right;">{row['null_count']:,}</td>
            <td style="padding: 8px; text-align: right; color: {color}; font-weight: bold;">{row['fill_pct']:.1f}%</td>
        </tr>
        """)
    
    sections.append("</table></div>")
    
    # Footer
    sections.append("""
    <div style="margin-top: 40px; padding-top: 20px; border-top: 2px solid #ecf0f1; color: #7f8c8d; text-align: center;">
        <p>BioMetaHarmonizer Summary Report | Generated by generate_summary_report.py</p>
    </div>
    </div>
    """)
    
    # Write HTML
    html_content = "\n".join(sections)
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BioMetaHarmonizer Summary Report</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
</head>
<body style="margin: 0; background-color: #ffffff;">
{html_content}
</body>
</html>"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_html, encoding="utf-8")
    
    logger.info("HTML report written to %s", output_path)
    return output_path


def generate_json_metrics(df: pd.DataFrame, fill_df: pd.DataFrame) -> Dict:
    """Generate JSON metrics for machine processing."""
    metrics = {
        "generated_at": datetime.now().isoformat(),
        "total_records": len(df),
        "total_columns": len(df.columns),
        "average_fill_rate": round(fill_df["fill_pct"].mean(), 2),
        "columns_by_quality": {
            "high_quality_80_plus": int((fill_df["fill_pct"] >= 80).sum()),
            "medium_quality_50_80": int(((fill_df["fill_pct"] >= 50) & (fill_df["fill_pct"] < 80)).sum()),
            "low_quality_below_50": int((fill_df["fill_pct"] < 50).sum())
        },
        "category_summary": {},
        "column_details": fill_df.to_dict(orient="records"),
        "extra_attributes_analysis": generate_extra_attributes_analysis(df)
    }
    
    # Category-wise summary
    for category in COLUMN_CATEGORIES.keys():
        cat_data = fill_df[fill_df["category"] == category]
        if len(cat_data) > 0:
            metrics["category_summary"][category] = {
                "column_count": len(cat_data),
                "avg_fill_rate": round(cat_data["fill_pct"].mean(), 2),
                "min_fill_rate": round(cat_data["fill_pct"].min(), 2),
                "max_fill_rate": round(cat_data["fill_pct"].max(), 2)
            }
    
    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Generate comprehensive summary report for BioMetaHarmonizer output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate HTML report only:
  python scripts/generate_summary_report.py --input harmonized.csv --output report.html
  
  # Generate all formats:
  python scripts/generate_summary_report.py \\
      --input harmonized.csv \\
      --output-dir reports/ \\
      --formats html json csv
  
  # Generate with verbose logging:
  python scripts/generate_summary_report.py -i harmonized.csv -o report.html -v
        """
    )
    
    parser.add_argument("--input", "-i", required=True, metavar="FILE",
                        help="Input harmonized CSV/TSV/Excel/Parquet file")
    parser.add_argument("--output", "-o", metavar="FILE", default=None,
                        help="Output file path (format inferred from extension)")
    parser.add_argument("--output-dir", "-d", metavar="DIR", default=None,
                        help="Output directory (generates multiple formats)")
    parser.add_argument("--formats", "-f", nargs="+", 
                        choices=["html", "json", "csv"], default=["html"],
                        help="Output formats (default: html)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Determine input format
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)
    
    suffix = input_path.suffix.lower()
    if suffix in [".csv", ".tsv", ".txt"]:
        df = pd.read_csv(input_path)
    elif suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(input_path)
    elif suffix == ".parquet":
        df = pd.read_parquet(input_path)
    else:
        logger.error("Unsupported input format: %s", suffix)
        sys.exit(1)
    
    logger.info("Loaded %d records x %d columns from %s", len(df), len(df.columns), input_path)
    
    # Compute fill rates
    fill_df = compute_fill_rates(df)
    
    # Determine output strategy
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_name = input_path.stem
        
        for fmt in args.formats:
            if fmt == "html":
                out_path = output_dir / f"{base_name}_report.html"
                generate_full_html_report(df, fill_df, out_path)
            elif fmt == "json":
                out_path = output_dir / f"{base_name}_metrics.json"
                metrics = generate_json_metrics(df, fill_df)
                out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
                logger.info("JSON metrics written to %s", out_path)
            elif fmt == "csv":
                out_path = output_dir / f"{base_name}_fill_rates.csv"
                fill_df.to_csv(out_path, index=False)
                logger.info("Fill rates CSV written to %s", out_path)
    elif args.output:
        output_path = Path(args.output)
        suffix = output_path.suffix.lower()
        
        if suffix == ".html":
            generate_full_html_report(df, fill_df, output_path)
        elif suffix == ".json":
            metrics = generate_json_metrics(df, fill_df)
            output_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            logger.info("JSON metrics written to %s", output_path)
        elif suffix == ".csv":
            fill_df.to_csv(output_path, index=False)
            logger.info("Fill rates CSV written to %s", output_path)
        else:
            logger.error("Unsupported output format: %s", suffix)
            sys.exit(1)
    else:
        logger.error("Either --output or --output-dir must be specified")
        sys.exit(1)
    
    print(f"Done. Summary report generated successfully.", file=sys.stdout)


if __name__ == "__main__":
    main()
