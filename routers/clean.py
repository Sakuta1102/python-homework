import json
import io
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from models.schemas import CleanConfig, CleanResponse, FillStrategy
from services.cleaner import load_dataframe, clean_dataframe

router = APIRouter(prefix="/clean", tags=["clean"])


@router.post("/upload", response_model=CleanResponse)
async def clean_file(
    file: UploadFile = File(...),
    drop_duplicates: bool = Form(True),
    fill_missing_strategy: FillStrategy = Form(FillStrategy.mean),
    fill_constant_value: str = Form(None),
    drop_columns: str = Form(""),
    strip_whitespace: bool = Form(True),
    convert_types: bool = Form(True),
):
    content = await file.read()
    try:
        df = load_dataframe(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = CleanConfig(
        drop_duplicates=drop_duplicates,
        fill_missing_strategy=fill_missing_strategy,
        fill_constant_value=fill_constant_value,
        drop_columns=[c.strip() for c in drop_columns.split(",") if c.strip()],
        strip_whitespace=strip_whitespace,
        convert_types=convert_types,
    )

    cleaned_df, stats = clean_dataframe(df, config)
    preview = cleaned_df.head(10).fillna("").to_dict(orient="records")

    return CleanResponse(
        original_rows=stats["original_rows"],
        cleaned_rows=stats["cleaned_rows"],
        dropped_rows=stats["original_rows"] - stats["cleaned_rows"],
        original_columns=stats["original_columns"],
        cleaned_columns=stats["cleaned_columns"],
        missing_filled=stats["missing_filled"],
        duplicates_removed=stats["duplicates_removed"],
        preview=preview,
        message="数据清洗完成",
    )


@router.post("/upload/download")
async def clean_and_download(
    file: UploadFile = File(...),
    drop_duplicates: bool = Form(True),
    fill_missing_strategy: FillStrategy = Form(FillStrategy.mean),
    fill_constant_value: str = Form(None),
    drop_columns: str = Form(""),
    strip_whitespace: bool = Form(True),
    convert_types: bool = Form(True),
):
    content = await file.read()
    try:
        df = load_dataframe(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = CleanConfig(
        drop_duplicates=drop_duplicates,
        fill_missing_strategy=fill_missing_strategy,
        fill_constant_value=fill_constant_value,
        drop_columns=[c.strip() for c in drop_columns.split(",") if c.strip()],
        strip_whitespace=strip_whitespace,
        convert_types=convert_types,
    )

    cleaned_df, _ = clean_dataframe(df, config)

    output = io.BytesIO()
    cleaned_df.to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cleaned_{file.filename}"},
    )
