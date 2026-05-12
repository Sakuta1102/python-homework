from pydantic import BaseModel, Field
from typing import Optional, List, Any
from enum import Enum


class FillStrategy(str, Enum):
    mean = "mean"
    median = "median"
    mode = "mode"
    drop = "drop"
    constant = "constant"


class CleanConfig(BaseModel):
    drop_duplicates: bool = True
    fill_missing_strategy: FillStrategy = FillStrategy.mean
    fill_constant_value: Optional[Any] = None
    drop_columns: List[str] = Field(default_factory=list)
    strip_whitespace: bool = True
    convert_types: bool = True


class CleanResponse(BaseModel):
    original_rows: int
    cleaned_rows: int
    dropped_rows: int
    original_columns: int
    cleaned_columns: int
    missing_filled: int
    duplicates_removed: int
    preview: List[dict]
    message: str
