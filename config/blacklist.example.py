"""
合法邮箱白名单模板。

⚠️ 部署时复制为 `config/blacklist.py`(已加入 .gitignore),由反欺诈团队
   填入真实白名单。本文件只是占位,真实数据不入仓库。

注意:黑产关键词 BLACKLIST_KEYWORDS 不在这里 —— 已切到飞书在线 sheet
维护,服务启动时由 config/projects.py 通过 FEISHU_BLACKLIST_KEYWORDS_SHEET_ID
拉取(见 .env.example)。
"""

# 合法邮箱域名:NOT LIKE 用,排除主流邮箱后剩下的视为可疑
VIRTUAL_EMAIL_NOTLIKE: list[str] = [
    # "@gmail.", "@yahoo.", ...
]
