"""
飞书电子表格客户端。

支持两种写入方式：
  1. write_to_wiki(wiki_token, rows)  —— 自动从知识库 token 解析出表格 token 和 sheet
  2. write_rows(spreadsheet_token, sheet_id, rows)  —— 直接写入（需要明确的 token）

所需环境变量：
  FEISHU_APP_ID      企业自建应用的 App ID
  FEISHU_APP_SECRET  企业自建应用的 App Secret
"""

import os
import httpx
from typing import Any

BASE_URL = "https://open.feishu.cn"


class FeishuClient:
    def __init__(self) -> None:
        self._app_id = os.getenv("FEISHU_APP_ID", "")
        self._app_secret = os.getenv("FEISHU_APP_SECRET", "")
        self._token: str | None = None

    def _tenant_token(self) -> str:
        if self._token:
            return self._token
        resp = httpx.post(
            f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"飞书获取 token 失败：{body}")
        self._token = body["tenant_access_token"]
        return self._token

    def _auth_token(self) -> str:
        from routers.feishu_auth import get_valid_user_token
        user_token = get_valid_user_token()
        if user_token:
            return user_token
        return self._tenant_token()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth_token()}",
            "Content-Type": "application/json",
        }

    def _resolve_wiki_token(self, wiki_token: str, sheet_title: str = "") -> tuple[str, str]:
        """
        将知识库页面 token 解析为 (spreadsheet_token, sheet_id)。
        sheet_title 为空时取第一个 sheet。
        """
        with httpx.Client(headers=self._headers(), timeout=15) as client:
            resp = client.get(
                f"{BASE_URL}/open-apis/wiki/v2/spaces/get_node",
                params={"token": wiki_token},
            )
            print(f"[Feishu] Wiki 节点查询响应 {resp.status_code}: {resp.text[:800]}", flush=True)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(f"Wiki 节点查询失败：{body}")
            node = body["data"]["node"]

        if node["obj_type"] != "sheet":
            raise ValueError(
                f"该知识库页面不是电子表格（类型为 {node['obj_type']}），"
                "请确认链接指向的是电子表格而非文档"
            )

        spreadsheet_token: str = node["obj_token"]

        with httpx.Client(headers=self._headers(), timeout=15) as client:
            resp = client.get(
                f"{BASE_URL}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets",
            )
            resp.raise_for_status()
            sheets: list[dict] = resp.json()["data"]["sheets"]

        if sheet_title:
            matched = [s for s in sheets if s.get("title") == sheet_title]
            if not matched:
                titles = [s.get("title") for s in sheets]
                raise ValueError(f"找不到 sheet '{sheet_title}'，现有 sheets：{titles}")
            sheet_id = matched[0]["sheet_id"]
        else:
            sheet_id = sheets[0]["sheet_id"]

        return spreadsheet_token, sheet_id

    def write_to_wiki(
        self,
        wiki_token: str,
        rows: list[dict[str, Any]],
        sheet_title: str = "",
        start_row: int = 1,
    ) -> None:
        """通过知识库 token 自动解析并写入对应电子表格。"""
        spreadsheet_token, sheet_id = self._resolve_wiki_token(wiki_token, sheet_title)
        self.write_rows(spreadsheet_token, sheet_id, rows, start_row)

    def _read_rows(self, spreadsheet_token: str, sheet_id: str, start_row: int = 1) -> list[list]:
        """读取 sheet 现有内容，返回二维列表。"""
        range_str = f"{sheet_id}!A{start_row}:Z5000"
        url = f"{BASE_URL}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_str}"
        with httpx.Client(headers=self._headers(), timeout=15) as client:
            resp = client.get(url)
            resp.raise_for_status()
            body = resp.json()
        values: list[list] = body.get("data", {}).get("valueRange", {}).get("values") or []
        # 去掉尾部全为空的行
        while values and all((c is None or c == "") for c in values[-1]):
            values.pop()
        print(f"[Feishu] 读取现有数据（去除空尾行后）: {len(values)} 行", flush=True)
        return values

    def write_rows(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        rows: list[dict[str, Any]],
        start_row: int = 1,
    ) -> None:
        """前插写入：新数据在最上方，与已有数据之间空一行。"""
        if not rows:
            print("[Feishu] write_rows: rows 为空，跳过写入", flush=True)
            return

        columns = list(rows[0].keys())
        new_block = [columns] + [[str(row.get(c, "")) for c in columns] for row in rows]

        existing = self._read_rows(spreadsheet_token, sheet_id, start_row)
        if existing:
            num_cols = max(len(columns), max((len(r) for r in existing), default=0))
            combined = new_block + [[""] * num_cols] + existing
        else:
            combined = new_block

        max_cols = max(len(r) for r in combined if r)
        end_col = _col_letter(max_cols)
        end_row = start_row + len(combined) - 1
        range_str = f"{sheet_id}!A{start_row}:{end_col}{end_row}"

        print(f"[Feishu] 前插 {len(rows)} 行，合计 {len(combined)} 行，写入 {range_str}", flush=True)
        url = f"{BASE_URL}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        payload = {"valueRange": {"range": range_str, "values": combined}}

        with httpx.Client(headers=self._headers(), timeout=30) as client:
            resp = client.put(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(f"飞书写入失败：{body}")


def send_webhook_notification(
    webhook_url: str,
    start_date: str,
    end_date: str,
    results: list[dict],
) -> None:
    """向飞书群机器人 Webhook 推送执行结果卡片消息。"""
    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    total_rows = sum(r["rows_written"] for r in succeeded)

    wiki_token = os.getenv("FEISHU_WIKI_TOKEN", "")
    doc_url = f"https://moonton.feishu.cn/wiki/{wiki_token}" if wiki_token else ""
    link = f"　[查看数据表格]({doc_url})" if doc_url else ""
    lines = [f"**日期范围：** {start_date} ~ {end_date}{link}\n"]
    for r in results:
        if r["success"]:
            lines.append(f"✅ {r['name']} · {r['rows_written']} 行")
        else:
            lines.append(f"❌ {r['name']} · {r['error'][:200]}")
    lines.append(f"\n**共写入 {total_rows} 行**")

    all_ok = len(failed) == 0
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "数据清洗执行完毕" if all_ok else f"数据清洗完毕（{len(failed)} 项失败）",
                },
                "template": "green" if all_ok else "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "\n".join(lines)},
                }
            ],
        },
    }
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[Feishu] 群通知已发送", flush=True)
    except Exception as exc:
        print(f"[Feishu] 群通知发送失败：{exc}", flush=True)


def _col_letter(n: int) -> str:
    """将列数转换为 Excel 列字母，如 1→A, 26→Z, 27→AA。"""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result
