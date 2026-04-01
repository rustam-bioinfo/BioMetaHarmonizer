import json
import pandas as pd
from pathlib import Path
from rapidfuzz import process, fuzz


class KeyMapper:
    """
    Module 2: Key Harmonization.
    Loads a JSON schema and maps raw DataFrame column names
    to standard NCBI keys using exact matching and fuzzy fallback.

    When multiple raw columns map to the same standard key,
    they are coalesced in order (first non-null value wins).
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

    def map_columns(self, df):
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
        self._warn_missing_mandatory(df)
        return df

    def _coalesce_duplicates(self, df):
        """
        When multiple raw columns rename to the same standard key,
        collapse them into one column using first-non-null coalescing.
        Unrecognized columns are passed through unchanged.
        """
        seen = {}
        for col in df.columns:
            if col not in seen:
                seen[col] = [col]
            else:
                seen[col].append(col)

        dedup_cols = {}
        for standard_key, occurrences in seen.items():
            if len(occurrences) == 1:
                dedup_cols[standard_key] = df[standard_key]
            else:
                coalesced = df.iloc[:, [df.columns.get_loc(c) for c in
                    [standard_key] * len(occurrences)]].bfill(axis=1).iloc[:, 0]
                dedup_cols[standard_key] = coalesced

        cols_in_order = list(dict.fromkeys(df.columns))
        result = pd.DataFrame(
            {col: dedup_cols[col] for col in cols_in_order},
            index=df.index
        )
        duped = [k for k, v in seen.items() if len(v) > 1]
        if duped:
            print(f"[INFO] Coalesced duplicate columns: {duped}")
        return result

    def _warn_missing_mandatory(self, df):
        mandatory = [f["standard_key"] for f in self.fields if f["mandatory"]]
        for key in mandatory:
            if key not in df.columns:
                print(f"[WARNING] Mandatory field '{key}' not found in dataset.")

    def get_parser_routing(self):
        return {f["standard_key"]: f["parser"] for f in self.fields}
