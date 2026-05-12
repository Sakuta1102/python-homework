"""玩家数据手动上传 + 黑产关键词检查 + 写回飞书。"""
import os
import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException

from config.blacklist import BLACKLIST_KEYWORDS
from services.cleaner import load_dataframe
from services.feishu import FeishuClient

router = APIRouter(prefix="/upload", tags=["upload"])

_FORM_NEW_EMAIL_ALIASES = [
    "form_new_email", "form_email_new", "new_email",
    "form-new-email", "form_new_email地址",
    "更换后邮箱", "新邮箱",
]


def _resolve_column(columns, aliases):
    norm = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        hit = norm.get(alias.lower())
        if hit is not None:
            return hit
    return None


def _has_blacklist(value, keywords_lower):
    if pd.isna(value):
        return False
    s = str(value).lower()
    return any(kw in s for kw in keywords_lower)


@router.post("/blacklist-check")
async def blacklist_check(file: UploadFile = File(...)):
    """读取上传的 csv/xlsx/json,过滤 form_new_email 命中黑产词的行,写回飞书。"""
    spreadsheet_token = os.getenv("FEISHU_SPREADSHEET_TOKEN", "")
    sheet_id = os.getenv("FEISHU_BLACKLIST_CHECK_SHEET_ID", "")
    if not spreadsheet_token or not sheet_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "缺少环境变量 FEISHU_SPREADSHEET_TOKEN 或 "
                "FEISHU_BLACKLIST_CHECK_SHEET_ID。先在飞书新建/确认目标 sheet,"
                "把 sheet_id 写入服务器 .env 后重启服务。"
            ),
        )

    content = await file.read()
    try:
        df = load_dataframe(content, file.filename or "uploaded")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    column = _resolve_column(df.columns, _FORM_NEW_EMAIL_ALIASES)
    if column is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"找不到 form_new_email 列。文件里的列名:{list(df.columns)};"
                f"接受的别名:{_FORM_NEW_EMAIL_ALIASES}"
            ),
        )

    keywords_lower = [kw.lower() for kw in BLACKLIST_KEYWORDS]
    mask = df[column].apply(lambda v: _has_blacklist(v, keywords_lower))
    hits = df[mask]
    hit_count = int(mask.sum())

    if hit_count > 0:
        rows = hits.fillna("").astype(str).to_dict(orient="records")
        FeishuClient().write_rows(
            spreadsheet_token=spreadsheet_token,
            sheet_id=sheet_id,
            rows=rows,
        )

    return {
        "filename": file.filename,
        "total_rows": int(len(df)),
        "hit_count": hit_count,
        "column_used": column,
        "written_to_feishu": hit_count > 0,
        "message": (
            f"共 {len(df)} 行,命中 {hit_count} 行"
            + (",已写入飞书" if hit_count > 0 else ",无命中")
        ),
    }
