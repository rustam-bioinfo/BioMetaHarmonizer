import re
import json
import pandas as pd
from pathlib import Path
from rapidfuzz import process, fuzz


_PROTECTED_COLUMNS = frozenset([
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
])

_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')

_DEFAULT_SCHEMA    = Path(__file__).parent.parent.parent / "schemas" / "unified.json"
_DEFAULT_MANDATORY = Path(__file__).parent.parent.parent / "schemas" / "mandatory_fields.json"

# packages with fewer records than this are skipped in mandatory field warnings
MIN_WARN_GROUP_SIZE = 10


class KeyMapper:
    """
    Module 2: Key Harmonization.

    Loads a unified JSON schema and maps raw DataFrame column names
    to standard NCBI keys using exact matching and fuzzy fallback.

    Mandatory field validation is per-package: each record's ncbi_package
    value is looked up in mandatory_fields.json and fill rates are reported
    per package rather than as a single global check. Packages with fewer
    than MIN_WARN_GROUP_SIZE records are silently skipped to avoid noise
    from singleton or near-singleton submissions.

    When multiple raw columns map to the same standard key,
    they are coalesced (first non-null value wins).
    """

    FUZZY_THRESHOLD = 85

    def __init__(self, schema_path=None, mandatory_path=None):
        schema_path = Path(schema_path) if schema_path else _DEFAULT_SCHEMA
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {schema_path}")
        with open(schema_path, "r") as f:
            self.schema = json.load(f)
        self.fields = self.schema["fields"]
        self._build_lookup()

        mandatory_path = Path(mandatory_path) if mandatory_path else _DEFAULT_MANDATORY
        if mandatory_path.exists():
            with open(mandatory_path, "r") as f:
                raw = json.load(f)
            self.mandatory = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            self.mandatory = {"default": ["collection_date", "geo_loc_name", "isolate"]}

    def _build_lookup(self):
        self.lookup = {}
        for field in self.fields:
            standard_key = field["standard_key"]
            self.lookup[standard_key.lower()] = standard_key
            for syn in field["synonyms"]:
                self.lookup[syn.lower()] = standard_key

    def map_columns(self, df, drop_sparse=5, drop_junk=True):
        """
        Harmonize column names, coalesce duplicates, clean junk/sparse
        columns, and report per-package mandatory field fill rates.

        Parameters
        ----------
        df : pd.DataFrame
            Raw ingested DataFrame.
        drop_sparse : int, default 5
            Drop columns with fewer than drop_sparse non-null values.
            Set to 0 to disable. Protected structural columns are never dropped.
        drop_junk : bool, default True
            Drop columns whose names look like person names or submitter
            artifacts. Protected structural columns are never dropped.
        """
        rename_map = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if col_lower in self.lookup:
                rename_map[col] = self.lookup[col_lower]
            else:
                result = process.extractOne(
                    col_lower,
                    self.lookup.keys(),
                    scorer=fuzz.token_sort_ratio
                )
                if result is not None:
                    match, score, _ = result
                    if score >= self.FUZZY_THRESHOLD:
                        rename_map[col] = self.lookup[match]

        df = df.rename(columns=rename_map)
        df = self._coalesce_duplicates(df)

        if drop_junk:
            df = self._drop_junk_columns(df)

        if drop_sparse and drop_sparse > 0:
            df = self._drop_sparse_columns(df, threshold=drop_sparse)

        self._warn_missing_mandatory(df)
        return df

    def _drop_junk_columns(self, df):
        junk = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and _PERSON_NAME_RE.match(col)
        ]
        if junk:
            print(f"[INFO] Dropping {len(junk)} junk columns (person names / artifacts): {junk}")
            df = df.drop(columns=junk)
        return df

    def _drop_sparse_columns(self, df, threshold):
        non_null = df.notna().sum()
        sparse = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and non_null[col] < threshold
        ]
        if sparse:
            print(f"[INFO] Dropping {len(sparse)} sparse columns (< {threshold} non-null values).")
            df = df.drop(columns=sparse)
        return df

    def _coalesce_duplicates(self, df):
        """
        When multiple raw columns rename to the same standard key,
        collapse them into one column using first-non-null coalescing.
        """
        if not df.columns.duplicated().any():
            return df

        seen = set()
        output_cols = {}
        duped = []

        for col in df.columns:
            if col not in seen:
                seen.add(col)
                block = df[col]
                if isinstance(block, pd.DataFrame):
                    duped.append(col)
                    coalesced = block.iloc[:, 0]
                    for i in range(1, block.shape[1]):
                        coalesced = coalesced.combine_first(block.iloc[:, i])
                    output_cols[col] = coalesced
                else:
                    output_cols[col] = block

        if duped:
            print(f"[INFO] Coalesced duplicate columns: {duped}")

        return pd.DataFrame(output_cols, index=df.index)

    def _warn_missing_mandatory(self, df):
        """
        For each ncbi_package group in df with >= MIN_WARN_GROUP_SIZE records,
        check fill rate of mandatory fields from mandatory_fields.json and
        warn when fill < 50%. Falls back to 'default' for unknown packages.
        Packages below MIN_WARN_GROUP_SIZE are silently skipped.
        """
        if "ncbi_package" not in df.columns:
            return

        for pkg, group in df.groupby("ncbi_package", dropna=False):
            n = len(group)
            if n < MIN_WARN_GROUP_SIZE:
                continue
            pkg_key  = pkg if pkg in self.mandatory else "default"
            required = self.mandatory.get(pkg_key, [])
            for field in required:
                if field not in df.columns:
                    print(f"[WARNING] [{pkg}] mandatory field '{field}' absent from dataset entirely.")
                else:
                    fill = group[field].notna().sum()
                    pct  = fill / n * 100
                    if pct < 50:
                        print(f"[WARNING] [{pkg}] mandatory field '{field}' fill rate: {fill}/{n} ({pct:.0f}%).")

    def get_parser_routing(self):
        return {f["standard_key"]: f["parser"] for f in self.fields}
