import os
from dataclasses import dataclass, field

try:
    from config.blacklist import VIRTUAL_EMAIL_NOTLIKE
except ImportError as exc:
    raise RuntimeError(
        "缺少 config/blacklist.py。请从 config/blacklist.example.py 复制一份并由"
        "反欺诈团队填入合法邮箱白名单。该文件已在 .gitignore 中,不会被提交。"
    ) from exc


# ── 黑产关键词 (写死在代码里, 跟仓库一起部署) ───────────────────────────────
# 仅用于 routers/upload.py 的纯 Python 过滤; 数据清洗 SQL 里的 LIKE 链是另写在
# _SQL_BLACKLIST_EMAIL / _SQL_BLACKLIST_NEW_EMAIL 中的(用户重新贴 SQL 时直接覆盖
# 那两个常量)。改词时记得两边同步,否则上传查询和数据清洗筛选会不一致。
BLACKLIST_KEYWORDS: list[str] = [
    "accfresh", "acc", "account", "admin", "akun", "akunml", "buy", "claim",
    "codashop", "code", "collector", "confirm", "csmlbb", "csmobile", "csmoonton",
    "custom", "dadun", "diamond", "donotreply", "dunski", "dunsky", "dunsqi",
    "evostv", "evylysn", "free", "freesourcecodes", "fresh", "freshml", "gufum",
    "gusion", "hack", "hacker", "help", "ibenkscr", "invoice", "kode", "kuccing",
    "limit", "midman", "ml", "mlbb", "mlbbcs", "mobileleg", "mobilelegend",
    "mobilelelegend", "moontod", "moonton", "pack", "payment", "recover", "redeem",
    "register", "retrieve", "rixx", "sell", "server", "service", "shop", "skin",
    "spinjela", "stevarazu", "stevenfebriyan", "stock", "stockgamer", "stok",
    "stolml", "store", "sukacash", "tohru.org", "unban", "unpak", "untukhb",
    "vaylyn", "verifi", "verify", "ganteng", "bns", "chou", "xvier", "kof",
    "hxh", "hunter", "onic", "@hi2.in", "clashofclans", "prize", "narruto",
    "vonsy", "moskov", "check", "moderator", "anjing", "metrozero", "hok",
    "dias", "sultan", "granger", "stox", "Bengkel", "banget", "gameshub.id",
    "active", "deltajohnsons.com", "customer",
]


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


# ── 共用平铺 SELECT 基础（查询6:虚拟邮箱）────────────────────────────────────

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


# ── 查询6:虚拟邮箱白名单 ────────────────────────────────────────────────────

_SQL_VIRTUAL_EMAIL = _SQL_FLAT_BASE + _not_like("form_email", VIRTUAL_EMAIL_NOTLIKE)


# ── 查询7-8: 黑产关键词筛选 (SQL 写死在代码里, 直接发给 Kyuubi) ────────────────
# 关键词改动:用户重新贴 SQL → 直接覆盖下面两个常量。
# 配套的 BLACKLIST_KEYWORDS 列表(在 config/blacklist.py)只给 routers/upload.py
# 的纯 Python 过滤用,改 SQL 时记得同步那个列表。

_SQL_BLACKLIST_EMAIL = """
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
            SELECT  replace(
                        replace(replace(event_data, '\\\\x', "temp"), '\\\\\\\\', '\\\\'),
                        "recv.mt_account_bind_email",
                        "mt_account_bind_email"
                    ) AS event_datas,
                    logymd,
                    roleid,
                    zoneid,
                    act_type,
                    TIME
            FROM    mtwb_ods.php_events
            WHERE   act_type = "elva"
            AND     logymd BETWEEN '{start_date}' AND '{end_date}'
        ) a
WHERE   (
            get_json_object(event_datas, '$.mlbb_game_response_data') = '{{"result":"0"}}'
             OR get_json_object(event_datas, '$.mt_account_bind_email') = '{{"result":"0"}}'
        )
AND     act_type = "elva"
AND     get_json_object(event_datas, '$.form_email') != ""
AND     get_json_object(event_datas, '$.form_new_email') != ""
AND     (
            lower(get_json_object (event_datas, '$.form_email')) LIKE "%accfresh%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%acc%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%account%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%admin%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%akun%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%akunml%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%buy%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%claim%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%codashop%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%code%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%collector%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%confirm%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%csmlbb%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%csmobile%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%csmoonton%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%custom%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%dadun%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%diamond%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%donotreply%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%dunski%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%dunsky%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%dunsqi%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%evostv%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%evylysn%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%free%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%freesourcecodes%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%fresh%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%freshml%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%gufum%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%gusion%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%hack%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%hacker%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%help%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%ibenkscr%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%invoice%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%kode%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%kuccing%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%limit%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%midman%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%ml%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%mlbb%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%mlbbcs%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%mobileleg%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%mobilelegend%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%mobilelelegend%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%moontod%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%moonton%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%pack%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%payment%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%recover%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%redeem%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%register%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%retrieve%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%rixx%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%sell%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%server%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%service%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%shop%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%skin%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%spinjela%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stevarazu%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stevenfebriyan%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stock%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stockgamer%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stok%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stolml%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%store%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%sukacash%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%tohru.org%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%unban%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%unpak%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%untukhb%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%vaylyn%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%verifi%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%verify%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%ganteng%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%bns%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%chou%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%xvier%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%kof%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%hxh%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%hunter%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%onic%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%@hi2.in%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%clashofclans%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%prize%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%narruto%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%vonsy%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%moskov%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%check%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%moderator%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%anjing%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%metrozero%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%hok%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%dias%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%sultan%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%granger%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%stox%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%Bengkel%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%banget%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%gameshub.id%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%active%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%deltajohnsons.com%"
            or lower(get_json_object (event_datas, '$.form_email')) like "%customer%"
        )
"""


_SQL_BLACKLIST_NEW_EMAIL = """
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
            SELECT  replace(
                        replace(replace(event_data, '\\\\x', "temp"), '\\\\\\\\', '\\\\'),
                        "recv.mt_account_bind_email",
                        "mt_account_bind_email"
                    ) AS event_datas,
                    logymd,
                    roleid,
                    zoneid,
                    act_type,
                    TIME
            FROM    mtwb_ods.php_events
            WHERE   act_type = "elva"
            AND     logymd BETWEEN '{start_date}' AND '{end_date}'
        ) a
WHERE   (
            get_json_object(event_datas, '$.mlbb_game_response_data') = '{{"result":"0"}}'
            OR get_json_object(event_datas, '$.mt_account_bind_email') = '{{"result":"0"}}'
        )
AND     act_type = "elva"
AND     get_json_object(event_datas, '$.form_email') != ""
AND     get_json_object(event_datas, '$.form_new_email') != ""
AND     (
            lower(get_json_object (event_datas, '$.form_new_email')) LIKE "%accfresh%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%acc%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%account%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%admin%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%akun%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%akunml%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%buy%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%claim%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%codashop%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%code%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%collector%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%confirm%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%csmlbb%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%csmobile%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%csmoonton%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%custom%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%dadun%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%diamond%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%donotreply%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%dunski%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%dunsky%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%dunsqi%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%evostv%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%evylysn%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%free%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%freesourcecodes%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%fresh%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%freshml%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%gufum%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%gusion%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%hack%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%hacker%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%help%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%ibenkscr%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%invoice%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%kode%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%kuccing%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%limit%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%midman%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%ml%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%mlbb%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%mlbbcs%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%mobileleg%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%mobilelegend%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%mobilelelegend%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%moontod%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%moonton%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%pack%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%payment%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%recover%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%redeem%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%register%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%retrieve%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%rixx%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%sell%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%server%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%service%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%shop%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%skin%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%spinjela%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stevarazu%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stevenfebriyan%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stock%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stockgamer%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stok%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stolml%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%store%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%sukacash%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%tohru.org%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%unban%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%unpak%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%untukhb%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%vaylyn%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%verifi%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%verify%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%ganteng%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%bns%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%chou%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%xvier%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%kof%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%hxh%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%hunter%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%onic%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%@hi2.in%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%clashofclans%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%prize%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%narruto%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%vonsy%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%moskov%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%check%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%moderator%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%anjing%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%metrozero%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%hok%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%dias%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%sultan%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%granger%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%stox%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%Bengkel%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%banget%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%gameshub.id%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%active%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%deltajohnsons.com%"
            or lower(get_json_object (event_datas, '$.form_new_email')) like "%customer%"
        )
ORDER BY
        get_json_object(event_datas, '$.form_new_email')
"""


# ── 项目列表 ─────────────────────────────────────────────────────────────────

_SPREADSHEET_TOKEN = os.getenv("FEISHU_SPREADSHEET_TOKEN", "")
_WIKI_TOKEN = os.getenv("FEISHU_WIKI_TOKEN", "")

PROJECTS: list[ProjectConfig] = [
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
    ProjectConfig(
        name="form_email黑产",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_BLACKLIST_EMAIL,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="bVm3JG",
    ),
    ProjectConfig(
        name="form_new_email黑产",
        feishu_wiki_token=_WIKI_TOKEN,
        sql_template=_SQL_BLACKLIST_NEW_EMAIL,
        feishu_spreadsheet_token=_SPREADSHEET_TOKEN,
        feishu_sheet_id="Q2OkrM",
    ),
]
