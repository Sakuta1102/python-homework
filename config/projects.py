import os
from dataclasses import dataclass, field

try:
    from config.blacklist import VIRTUAL_EMAIL_NOTLIKE
except ImportError as exc:
    raise RuntimeError(
        "缺少 config/blacklist.py。请从 config/blacklist.example.py 复制一份并由"
        "反欺诈团队填入合法邮箱白名单。该文件已在 .gitignore 中,不会被提交。"
    ) from exc


def fetch_blacklist_keywords() -> list[str]:
    """每次调用都从飞书电子表格指定 sheet(第一列)实时拉取黑产关键词。

    不缓存:词表只在飞书在线维护,运营改完保存,服务下一次执行就用新词表
    (无需重启)。代价是每次 pipeline / upload 多一次 ~300ms 的飞书 API
    调用,可以接受。

    由 FEISHU_BLACKLIST_KEYWORDS_SHEET_ID 指定 sheet,从 FEISHU_SPREADSHEET_TOKEN
    指定的电子表格读取。第一行如果是表头("关键词" / "keyword" / 类似)会跳过。
    """
    sheet_id = os.getenv("FEISHU_BLACKLIST_KEYWORDS_SHEET_ID", "")
    spreadsheet_token = os.getenv("FEISHU_SPREADSHEET_TOKEN", "")
    if not sheet_id or not spreadsheet_token:
        raise RuntimeError(
            "缺少 FEISHU_SPREADSHEET_TOKEN 或 FEISHU_BLACKLIST_KEYWORDS_SHEET_ID env。"
            "黑产关键词从飞书 sheet 第一列读取,这两个变量必须配置。"
        )
    from services.feishu import FeishuClient
    rows = FeishuClient().read_rows(spreadsheet_token, sheet_id)
    keywords: list[str] = []
    header_aliases = {"关键词", "keyword", "黑产关键词", "邮箱关键词"}
    for idx, row in enumerate(rows):
        if not row:
            continue
        v = row[0]
        if v is None or v == "":
            continue
        s = str(v).strip()
        if not s:
            continue
        if idx == 0 and s.lower() in {a.lower() for a in header_aliases}:
            continue
        keywords.append(s)
    if not keywords:
        raise RuntimeError(
            f"飞书 sheet {sheet_id} 第一列没读到任何关键词,请检查 sheet 内容与权限。"
        )
    return keywords


@dataclass
class ProjectConfig:
    name: str
    feishu_wiki_token: str        # 飞书知识库页面 token
    sql_template: str = ""        # SQL 模板，用 {start_date} / {end_date} 占位
    feishu_sheet_title: str = ""  # 目标 sheet 名，空字符串 = 写第一个 sheet
    feishu_start_row: int = 1
    feishu_spreadsheet_token: str = ""  # 直接指定，跳过 wiki 查询
    feishu_sheet_id: str = ""           # 直接指定，跳过 sheet 列表查询
    extra_params: dict = field(default_factory=dict)


# ── 共用 CTE（查询1-5）─────────────────────────────────────────────────────────

_SQL_CTE = """
WITH role_lost_acc_count AS (
  SELECT logymd,
         roleid,
         get_json_object(event_datas, '$.target_account_id') AS lost_acc_id,
         get_json_object(event_datas, '$.target_zone_id') AS lost_acc_server,
         lower(get_json_object(event_datas, '$.form_email')) AS form_email,
         lower(get_json_object(event_datas, '$.form_new_email')) AS form_new_email,
         get_json_object(event_datas, '$.source_device_id') AS device_id,
         lower(get_json_object(event_datas, '$.source_region')) AS source_region,
         lower(get_json_object(event_datas, '$.target_account_create_region')) AS target_account_create_region,
         get_json_object(event_datas, '$.shark_appeal_id') AS shark_appeal_id
  FROM
    (SELECT replace(replace(replace(event_data, '\\\\x', 'temp'),'\\\\\\\\','\\\\'), 'recv.mt_account_bind_email', 'mt_account_bind_email') AS event_datas,
            logymd, roleid, zoneid, act_type, TIME
     FROM mtwb_ods.php_events
     WHERE act_type = 'elva'
       AND logymd BETWEEN '{start_date}' AND '{end_date}') a
  WHERE (get_json_object(event_datas, '$.mlbb_game_response_data') = '{{"result":"0"}}'
         OR get_json_object(event_datas, '$.mt_account_bind_email') = '{{"result":"0"}}')
    AND act_type = 'elva'
    AND get_json_object(event_datas, '$.form_new_email') != ''
    AND get_json_object(event_datas, '$.form_email') != ''
)
"""

_SQL_UID_MULTI_ACC = _SQL_CTE + """
SELECT bb.*, aa.unique_lost_acc_count FROM
(SELECT roleid,
        COUNT(DISTINCT lost_acc_id) AS unique_lost_acc_count
 FROM role_lost_acc_count
 GROUP BY roleid
 HAVING COUNT(DISTINCT lost_acc_id) > 1) aa
LEFT JOIN
(SELECT logymd, roleid, lost_acc_id, lost_acc_server, form_email, form_new_email,
        device_id, source_region, target_account_create_region, shark_appeal_id
 FROM role_lost_acc_count) bb
ON aa.roleid = bb.roleid
"""

_SQL_DID_MULTI_ACC = _SQL_CTE + """
SELECT bb.*, aa.unique_lost_acc_count FROM
(SELECT device_id,
        COUNT(DISTINCT lost_acc_id) AS unique_lost_acc_count
 FROM role_lost_acc_count
 GROUP BY device_id
 HAVING COUNT(DISTINCT lost_acc_id) > 1) aa
LEFT JOIN
(SELECT logymd, roleid, lost_acc_id, lost_acc_server, form_email, form_new_email,
        device_id, source_region, target_account_create_region, shark_appeal_id
 FROM role_lost_acc_count) bb
ON aa.device_id = bb.device_id
"""

_SQL_TARGET_MULTI_RECOVERY = _SQL_CTE + """
SELECT bb.*, aa.unique_lost_acc_count FROM
(SELECT lost_acc_id,
        COUNT(lost_acc_id) AS unique_lost_acc_count
 FROM role_lost_acc_count
 GROUP BY lost_acc_id
 HAVING COUNT(*) > 3) aa
LEFT JOIN
(SELECT logymd, roleid, lost_acc_id, lost_acc_server, form_email, form_new_email,
        device_id, source_region, target_account_create_region, shark_appeal_id
 FROM role_lost_acc_count) bb
ON aa.lost_acc_id = bb.lost_acc_id
"""

_SQL_EMAIL_MULTI_ACC = _SQL_CTE + """
SELECT bb.*, aa.unique_lost_acc_count FROM
(SELECT form_email,
        COUNT(DISTINCT lost_acc_id) AS unique_lost_acc_count
 FROM role_lost_acc_count
 GROUP BY form_email
 HAVING COUNT(DISTINCT lost_acc_id) > 1) aa
LEFT JOIN
(SELECT logymd, roleid, lost_acc_id, lost_acc_server, form_email, form_new_email,
        device_id, source_region, target_account_create_region, shark_appeal_id
 FROM role_lost_acc_count) bb
ON aa.form_email = bb.form_email
"""

_SQL_NEW_EMAIL_MULTI_ACC = _SQL_CTE + """
SELECT bb.*, aa.unique_lost_acc_count FROM
(SELECT form_new_email,
        COUNT(DISTINCT lost_acc_id) AS unique_lost_acc_count
 FROM role_lost_acc_count
 GROUP BY form_new_email
 HAVING COUNT(DISTINCT lost_acc_id) > 1) aa
LEFT JOIN
(SELECT logymd, roleid, lost_acc_id, lost_acc_server, form_email, form_new_email,
        device_id, source_region, target_account_create_region, shark_appeal_id
 FROM role_lost_acc_count) bb
ON aa.form_new_email = bb.form_new_email
"""


# ── 共用平铺 SELECT 基础（查询6-8，无 CTE）────────────────────────────────────

_SQL_FLAT_BASE = """
SELECT  logymd,
        roleid,
        get_json_object(event_datas, '$.target_account_id') AS lost_acc_id,
        get_json_object(event_datas, '$.target_zone_id') AS lost_acc_server,
        lower(get_json_object(event_datas, '$.form_email')) AS form_email,
        lower(get_json_object(event_datas, '$.form_new_email')) AS form_new_email,
        get_json_object(event_datas, '$.source_device_id') AS device_id,
        lower(get_json_object(event_datas, '$.source_region')) AS source_region,
        lower(get_json_object(event_datas, '$.target_account_create_region')) AS target_account_create_region,
        get_json_object(event_datas, '$.shark_appeal_id') AS shark_appeal_id
FROM    (
            SELECT  replace(replace(replace(event_data, '\\\\x', 'temp'), '\\\\\\\\', '\\\\'), 'recv.mt_account_bind_email', 'mt_account_bind_email') AS event_datas,
                    logymd, roleid, zoneid, act_type, TIME
            FROM    mtwb_ods.php_events
            WHERE   act_type = 'elva'
            AND     logymd BETWEEN '{start_date}' AND '{end_date}'
        ) a
WHERE   (get_json_object(event_datas, '$.mlbb_game_response_data') = '{{"result":"0"}}'
         OR get_json_object(event_datas, '$.mt_account_bind_email') = '{{"result":"0"}}')
AND     act_type = 'elva'
AND     get_json_object(event_datas, '$.form_email') != ''
AND     get_json_object(event_datas, '$.form_new_email') != ''
"""


def _not_like(field: str, patterns: list[str]) -> str:
    return "\n".join(
        f"AND lower(get_json_object(event_datas, '$.{field}')) not like '%{p}%'"
        for p in patterns
    )


def _or_like(field: str, keywords: list[str]) -> str:
    lines = [
        f"    lower(get_json_object(event_datas, '$.{field}')) like '%{kw}%'"
        for kw in keywords
    ]
    return "AND (\n" + "\n    or ".join(lines) + "\n)"


# ── 查询6:虚拟邮箱白名单 ────────────────────────────────────────────────────
# 黑/白名单词表分两路:VIRTUAL_EMAIL_NOTLIKE 在 config/blacklist.py(gitignored);
# BLACKLIST_KEYWORDS 在飞书 sheet 实时维护,见 fetch_blacklist_keywords()。

_SQL_VIRTUAL_EMAIL = _SQL_FLAT_BASE + _not_like("form_email", VIRTUAL_EMAIL_NOTLIKE)


# ── 项目列表 ─────────────────────────────────────────────────────────────────

_SPREADSHEET_TOKEN = os.getenv("FEISHU_SPREADSHEET_TOKEN", "")
_WIKI_TOKEN = os.getenv("FEISHU_WIKI_TOKEN", "")

# 不依赖飞书黑名单的 6 个固定项目,模块加载时构建一次即可
_BASE_PROJECTS: list[ProjectConfig] = [
    ProjectConfig(
        name="来单uid找回多个账号",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_UID_MULTI_ACC,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="6c35ad",
    ),
    ProjectConfig(
        name="来单did找回多个账号",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_DID_MULTI_ACC,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="BV7U2v",
    ),
    ProjectConfig(
        name="同一目标账号被找回多次",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_TARGET_MULTI_RECOVERY,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="e5CHNt",
    ),
    ProjectConfig(
        name="来单邮箱找回N个不同账号",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_EMAIL_MULTI_ACC,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="CtOnvT",
    ),
    ProjectConfig(
        name="换绑后邮箱绑定N个不同账号",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_NEW_EMAIL_MULTI_ACC,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="im8FoX",
    ),
    ProjectConfig(
        name="form_email临时虚拟邮箱",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_VIRTUAL_EMAIL,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="FsZa3k",
    ),
]


def get_projects() -> list[ProjectConfig]:
    """返回完整的 8 个项目配置;调用时实时从飞书拉黑产关键词,组装最后两条 SQL。

    供 services/pipeline.run_pipeline 调用。每次 pipeline 触发都会重新拉关键词,
    保证用的是飞书 sheet 当前状态。
    """
    keywords = fetch_blacklist_keywords()
    sql_blacklist = _SQL_FLAT_BASE + _or_like("form_email", keywords)
    sql_blacklist_new = (
        _SQL_FLAT_BASE
        + _or_like("form_new_email", keywords)
        + "\nORDER BY lower(get_json_object(event_datas, '$.form_new_email'))"
    )
    return _BASE_PROJECTS + [
        ProjectConfig(
            name="form_email黑产",
            feishu_wiki_token=_WIKI_TOKEN,
            sql_template=sql_blacklist,
            feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
            feishu_sheet_id="bVm3JG",
        ),
        ProjectConfig(
            name="form_new_email黑产",
            feishu_wiki_token=_WIKI_TOKEN,
            sql_template=sql_blacklist_new,
            feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
            feishu_sheet_id="Q2OkrM",
        ),
    ]
