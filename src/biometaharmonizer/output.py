"""Module 6: Output -- write harmonized DataFrame to disk."""

import logging
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)

_VALID_FORMATS = ("csv", "tsv", "excel", "parquet")


def write(df: pd.DataFrame, path, fmt: str = "csv") -> Path:
    """
    Write a harmonized DataFrame to disk.

    Parameters
    ----------
    df : pd.DataFrame
        Harmonized DataFrame to write.
    path : str or Path
        Destination file path. Parent directories are created automatically.
    fmt : str, default "csv"
        Output format. One of: "csv", "tsv", "excel", "parquet".
        Case-insensitive.

    Returns
    -------
    Path
        Resolved absolute path to the written file.

    Raises
    ------
    ValueError
        If fmt is not one of the supported formats.
    """
    fmt = fmt.lower()
    if fmt not in _VALID_FORMATS:
        raise ValueError(
            f"Unsupported format {fmt!r}. Valid options: {', '.join(_VALID_FORMATS)}"
        )

    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        df.to_csv(path, index=False, encoding="utf-8")
    elif fmt == "tsv":
        df.to_csv(path, index=False, sep="\t", encoding="utf-8")
    elif fmt == "excel":
        df.to_excel(path, index=False, engine="openpyxl")
    elif fmt == "parquet":
        df.to_parquet(path, index=False, engine="pyarrow")

    logger.info(
        "Output written: %s (%d records, %d columns)",
        path, len(df), len(df.columns),
    )
    return path


def write_summary(df: pd.DataFrame, path) -> Path:
    """
    Write a fill-rate summary CSV for each column in df.

    Columns in output: column_name, non_null_count, fill_pct

    Parameters
    ----------
    df : pd.DataFrame
        Source DataFrame to summarize.
    path : str or Path
        Destination file path for the summary CSV.

    Returns
    -------
    Path
        Resolved absolute path to the written summary file.
    """
    n = len(df)
    rows = []
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        fill_pct = round(non_null / n * 100, 1) if n > 0 else 0.0
        rows.append({"column_name": col, "non_null_count": non_null, "fill_pct": fill_pct})

    summary = pd.DataFrame(rows)
    return write(summary, path, fmt="csv")
