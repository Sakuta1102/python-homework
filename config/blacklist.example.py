"""
反欺诈合法邮箱白名单模板。

⚠️ 部署时复制为 `config/blacklist.py`(已加入 .gitignore),由反欺诈团队
   填入真实白名单。本文件只是占位,真实数据不入仓库。

注意: 黑产关键词 BLACKLIST_KEYWORDS 写在 config/projects.py 里, 不在这里。
"""

# 合法邮箱域名:NOT LIKE 用,排除主流邮箱后剩下的视为可疑
VIRTUAL_EMAIL_NOTLIKE: list[str] = [
    # "@gmail.", "@yahoo.", ...
]
