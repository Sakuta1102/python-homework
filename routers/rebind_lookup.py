"""换绑邮箱查询:上传 uid 列表 -> 查每个 accountid 最新一条换绑日志 -> 给原表加 new_email 列写回飞书。"""
import os
import math
import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException

from services.cleaner import load_dataframe
from services.feishu import FeishuClient
from services.kyuubi import KyuubiClient

router = APIRouter(prefix="/rebind-lookup", tags=["rebind-lookup"])

# 输入文件中 uid 列的可能名称
_UID_ALIASES = [
    "uid", "UID",
    "accountid", "account_id", "AccountID",
    "user_id", "userid",
    "玩家uid", "玩家UID", "账号id", "账号ID",
]

# 默认目标 sheet:https://moonton.feishu.cn/wiki/A3FhwDKulifiN7kZ0xMcIoW3n5c?sheet=iaRiqB
_DEFAULT_WIKI_TOKEN = "A3FhwDKulifiN7kZ0xMcIoW3n5c"
_DEFAULT_SHEET_ID = "iaRiqB"

# IN 子句切片大小,Spark 处理几千个还行,太长会让 SQL 解析变慢
_BATCH_SIZE = 5000


def _resolve_column(columns, aliases):
    norm = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        hit = norm.get(alias.lower())
        if hit is not None:
            return hit
    return None


def _normalize_uid(v) -> str:
    """把 csv/excel 里的 uid 规范为字符串:NaN -> '';12345.0 -> '12345'。"""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
        return str(v).strip()
    return str(v).strip()


def _build_sql(accountids: list[str]) -> str:
    """每个 accountid 取 time 最新的一条,带上常用字段。"""
    quoted = ",".join(f"'{a}'" for a in accountids)
    return f"""
SELECT  time, account_name, old_email, accountid, new_email, logymd
FROM (
    SELECT  time, account_name, old_email, accountid, new_email, logymd,
            ROW_NUMBER() OVER (PARTITION BY accountid ORDER BY time DESC) AS rn
    FROM    ml_ods.mtaccountserver_account_rebind_email
    WHERE   CAST(accountid AS STRING) IN ({quoted})
) t
WHERE   rn = 1
""".strip()


@router.post("/query")
async def rebind_lookup(file: UploadFile = File(...)):
    """读取上传的 csv/xlsx/json,按 uid 列查 ml_ods.mtaccountserver_account_rebind_email
    每个 accountid 的最新换绑记录,给原表追加 new_email 列后写回飞书 sheet。"""
    wiki_token = os.getenv("FEISHU_REBIND_LOOKUP_WIKI_TOKEN", _DEFAULT_WIKI_TOKEN)
    sheet_id = os.getenv("FEISHU_REBIND_LOOKUP_SHEET_ID", _DEFAULT_SHEET_ID)

    content = await file.read()
    try:
        df = load_dataframe(content, file.filename or "uploaded")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    uid_col = _resolve_column(df.columns, _UID_ALIASES)
    if uid_col is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"找不到 uid 列。文件里的列名:{list(df.columns)};"
                f"接受的别名:{_UID_ALIASES}"
            ),
        )

    df["__uid_norm"] = df[uid_col].apply(_normalize_uid)
    accountids = sorted({u for u in df["__uid_norm"].tolist() if u})
    if not accountids:
        raise HTTPException(status_code=400, detail=f"列 {uid_col} 没有有效的 uid")

    print(
        f"[Rebind] 文件 {file.filename}, uid 列 {uid_col}, "
        f"原始 {len(df)} 行 -> 去重后 {len(accountids)} 个 accountid",
        flush=True,
    )

    kyuubi = KyuubiClient()
    new_email_map: dict[str, str] = {}

    for i in range(0, len(accountids), _BATCH_SIZE):
        batch = accountids[i : i + _BATCH_SIZE]
        sql = _build_sql(batch)
        rows = kyuubi.run_query(
            sql=sql,
            task_name=f"换绑邮箱查询 batch {i // _BATCH_SIZE + 1}/{math.ceil(len(accountids) / _BATCH_SIZE)}",
        )
        for r in rows:
            aid = _normalize_uid(r.get("accountid"))
            if aid:
                new_email_map[aid] = "" if r.get("new_email") is None else str(r["new_email"])

    df["new_email"] = df["__uid_norm"].map(new_email_map).fillna("")
    matched = int((df["new_email"] != "").sum())

    df = df.drop(columns=["__uid_norm"])
    rows_to_write = df.fillna("").astype(str).to_dict(orient="records")

    FeishuClient().write_to_wiki_sheet(
        wiki_token=wiki_token,
        sheet_id=sheet_id,
        rows=rows_to_write,
    )

    return {
        "filename": file.filename,
        "uid_column": uid_col,
        "total_rows": int(len(df)),
        "unique_accountids": len(accountids),
        "matched_rows": matched,
        "wiki_token": wiki_token,
        "sheet_id": sheet_id,
        "message": (
            f"共 {len(df)} 行,{len(accountids)} 个 accountid,"
            f"匹配到 {matched} 条最新换绑记录,已写入飞书"
        ),
    }
