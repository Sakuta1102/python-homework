"""
反欺诈邮箱黑名单 / 合法邮箱白名单。

⚠️ 真实业务规则不入仓库。
- 复制本文件为 `blacklist.py`(已加入 .gitignore)
- 由 IT/反欺诈团队提供实际词表

`config/projects.py` 在导入时会引用这两个列表。
"""

# 黑产关键词:命中即认定是高风险注册/找回邮箱
BLACKLIST_KEYWORDS: list[str] = [
    # "示例:把真实关键词填入 config/blacklist.py(本文件不要提交)"
]

# 合法邮箱域名:NOT LIKE 用,排除主流邮箱后剩下的视为可疑
VIRTUAL_EMAIL_NOTLIKE: list[str] = [
    # "@gmail.", "@yahoo.", ...  # 真实白名单填入 config/blacklist.py
]
