import re
import json
import pandas as pd
from pathlib import Path
from rapidfuzz import process, fuzz


# Structural columns always retained regardless of fill rate or name pattern.
_PROTECTED_COLUMNS = frozenset([
    "biosample_accession", "biosample_id", "sra_accession",
    "bioproject_accession", "sample_name_id", "submission_date",
    "last_update", "publication_date", "access", "status",
    "status_date", "title", "description_comment", "ncbi_package",
    "taxonomy_id", "taxonomy_name", "organism_name",
])

# Regex: two or more Title-Case words separated by spaces (person names).
# Matches 'Chao Pan', 'Rajeev K. Varshney', 'XianKai Liu', etc.
_PERSON_NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s[A-Z]\.?)?\s[A-Z][a-z]+$')


class KeyMapper:
    """
    Module 2: Key Harmonization.
    Loads a JSON schema and maps raw DataFrame column names
    to standard NCBI keys using exact matching and fuzzy fallback.

    When multiple raw columns map to the same standard key,
    they are coalesced in order (first non-null value wins).

    map_columns() also optionally drops:
      - Sparse columns (non-null count below drop_sparse threshold)
      - Junk columns (person names accidentally used as attribute keys)
    """

    FUZZY_THRESHOLD = 85

    def __init__(self, schema_path):
        schema_path = Path(schema_path)
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {schema_path}")
        with open(schema_path, "r") as f:
            self.schema = json.load(f)
        self.fields = self.schema["fields"]
        self._build_lookup()

    def _build_lookup(self):
        self.lookup = {}
        for field in self.fields:
            standard_key = field["standard_key"]
            self.lookup[standard_key.lower()] = standard_key
            for syn in field["synonyms"]:
                self.lookup[syn.lower()] = standard_key

    def map_columns(self, df, drop_sparse=5, drop_junk=True):
        """
        Harmonize column names, coalesce duplicates, warn on missing
        mandatory fields, and optionally clean junk/sparse columns.

        Parameters
        ----------
        df : pd.DataFrame
            Raw ingested DataFrame.
        drop_sparse : int, default 5
            Drop columns where non-null count < drop_sparse.
            Set to 0 to disable. Protected structural columns are
            never dropped regardless of fill rate.
        drop_junk : bool, default True
            Drop columns whose names look like person names or other
            submitter artifacts (e.g. 'Chao Pan', 'Urmi Halder').
            Protected structural columns are never dropped.
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
        """
        Drop columns whose names match person-name patterns or other
        known submitter artifact patterns. Protected columns are kept.
        """
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
        """
        Drop columns with fewer than `threshold` non-null values.
        Protected structural columns are always retained.
        """
        non_null = df.notna().sum()
        sparse = [
            col for col in df.columns
            if col not in _PROTECTED_COLUMNS
            and non_null[col] < threshold
        ]
        if sparse:
            print(f"[INFO] Dropping {len(sparse)} sparse columns "
                  f"(< {threshold} non-null values).")
            df = df.drop(columns=sparse)
        return df

    def _coalesce_duplicates(self, df):
        """
        When multiple raw columns rename to the same standard key,
        collapse them into one column using first-non-null coalescing.
        Handles duplicate column names safely without positional indexing.
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
        mandatory = [f["standard_key"] for f in self.fields if f["mandatory"]]
        for key in mandatory:
            if key not in df.columns:
                print(f"[WARNING] Mandatory field '{key}' not found in dataset.")

    def get_parser_routing(self):
        return {f["standard_key"]: f["parser"] for f in self.fields}
