import pandas as pd
import numpy as np
import io
from models.schemas import CleanConfig, FillStrategy


def load_dataframe(content: bytes, filename: str) -> pd.DataFrame:
    if filename.endswith(".csv"):
        try:
            return pd.read_csv(io.BytesIO(content), encoding="utf-8")
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(content), encoding="gbk")
    elif filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    elif filename.endswith(".json"):
        return pd.read_json(io.BytesIO(content))
    else:
        raise ValueError(f"Unsupported file format: {filename}")


def clean_dataframe(df: pd.DataFrame, config: CleanConfig) -> tuple[pd.DataFrame, dict]:
    stats = {
        "original_rows": len(df),
        "original_columns": len(df.columns),
        "duplicates_removed": 0,
        "missing_filled": 0,
    }

    # Drop specified columns
    cols_to_drop = [c for c in config.drop_columns if c in df.columns]
    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)

    # Strip whitespace from string columns
    if config.strip_whitespace:
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].str.strip()

    # Remove duplicates
    if config.drop_duplicates:
        before = len(df)
        df = df.drop_duplicates()
        stats["duplicates_removed"] = before - len(df)

    # Handle missing values
    missing_before = df.isnull().sum().sum()
    strategy = config.fill_missing_strategy

    if strategy == FillStrategy.drop:
        df = df.dropna()
    elif strategy == FillStrategy.constant:
        df = df.fillna(config.fill_constant_value)
    else:
        for col in df.columns:
            if df[col].isnull().any():
                if df[col].dtype in [np.float64, np.int64, float, int]:
                    if strategy == FillStrategy.mean:
                        df[col] = df[col].fillna(df[col].mean())
                    elif strategy == FillStrategy.median:
                        df[col] = df[col].fillna(df[col].median())
                    elif strategy == FillStrategy.mode:
                        df[col] = df[col].fillna(df[col].mode()[0])
                else:
                    df[col] = df[col].fillna(df[col].mode()[0] if not df[col].mode().empty else "")

    stats["missing_filled"] = int(missing_before - df.isnull().sum().sum())

    # Auto-convert types
    if config.convert_types:
        df = df.infer_objects()

    stats["cleaned_rows"] = len(df)
    stats["cleaned_columns"] = len(df.columns)
    return df, stats
