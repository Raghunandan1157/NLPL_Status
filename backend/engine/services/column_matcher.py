"""
Shared Column Matcher - fuzzy column name matching from HOURLY server.
Handles inconsistent column naming across Excel files.
"""
import re


def find_column(df, *expected_names):
    """
    Find a column in a DataFrame by trying multiple possible names.
    First tries exact match, then normalized (case-insensitive, no spaces/underscores).
    Returns the actual column name found, or None.
    """
    columns = list(df.columns)

    # Try exact match first
    for name in expected_names:
        if name in columns:
            return name

    # Try normalized match
    def normalize(s):
        return re.sub(r'[\s_]+', '', str(s).lower().strip())

    normalized_cols = {normalize(c): c for c in columns}
    for name in expected_names:
        norm = normalize(name)
        if norm in normalized_cols:
            return normalized_cols[norm]

    return None
