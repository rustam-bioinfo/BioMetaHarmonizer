import json
import pandas as pd
from pathlib import Path
from rapidfuzz import process, fuzz


class KeyMapper:
    """
    Module 2: Key Harmonization.
    Loads a JSON schema and maps raw DataFrame column names
    to standard NCBI keys using exact matching and fuzzy fallback.
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
                match, score, _ = process.extractOne(
                    col_lower,
                    self.lookup.keys(),
                    scorer=fuzz.token_sort_ratio
                )
                if score >= self.FUZZY_THRESHOLD:
                    rename_map[col] = self.lookup[match]
        df = df.rename(columns=rename_map)
        self._warn_missing_mandatory(df)
        return df

    def _warn_missing_mandatory(self, df):
        mandatory = [f["standard_key"] for f in self.fields if f["mandatory"]]
        for key in mandatory:
            if key not in df.columns:
                print(f"[WARNING] Mandatory field '{key}' not found in dataset.")

    def get_parser_routing(self):
        return {f["standard_key"]: f["parser"] for f in self.fields}
