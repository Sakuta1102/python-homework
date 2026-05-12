import json
import os
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["feishu-auth"])

_APP_ID = os.getenv("FEISHU_APP_ID", "")
_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
_REDIRECT_URI = "http://127.0.0.1:8000/feishu-auth-callback"
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "feishu_tokens.json")
_BASE = "https://open.feishu.cn"


def _app_access_token() -> str:
    resp = httpx.post(
        f"{_BASE}/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": _APP_ID, "app_secret": _APP_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["app_access_token"]


def load_tokens() -> dict:
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_tokens(data: dict) -> None:
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_valid_user_token() -> str | None:
    tokens = load_tokens()
    if not tokens:
        return None

    if time.time() < tokens.get("expires_at", 0) - 60:
        return tokens["access_token"]

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None

    try:
        aat = _app_access_token()
        resp = httpx.post(
            f"{_BASE}/open-apis/authen/v1/refresh_access_token",
            json={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {aat}"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            print(f"[Feishu] refresh_token 刷新失败：{body}")
            return None
        data = body["data"]
        new_tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": int(time.time()) + data["expires_in"],
        }
        save_tokens(new_tokens)
        print("[Feishu] user_access_token 已刷新")
        return new_tokens["access_token"]
    except Exception as e:
        print(f"[Feishu] 刷新 token 异常：{e}")
        return None


@router.get("/feishu-auth", include_in_schema=False)
def feishu_auth():
    params = urlencode({"redirect_uri": _REDIRECT_URI, "app_id": _APP_ID})
    url = f"{_BASE}/open-apis/authen/v1/index?{params}"
    return RedirectResponse(url)


@router.get("/feishu-auth-callback", include_in_schema=False)
def feishu_auth_callback(code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(f"<h3>授权失败：{error or '未收到 code'}</h3>", status_code=400)

    try:
        aat = _app_access_token()
        resp = httpx.post(
            f"{_BASE}/open-apis/authen/v1/access_token",
            json={"grant_type": "authorization_code", "code": code},
            headers={"Authorization": f"Bearer {aat}"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            return HTMLResponse(f"<h3>换 token 失败：{body}</h3>", status_code=400)

        data = body["data"]
        save_tokens({
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": int(time.time()) + data["expires_in"],
        })
        return HTMLResponse("<h3>✅ 授权成功！可以关闭此页面。</h3>")
    except Exception as e:
        return HTMLResponse(f"<h3>异常：{e}</h3>", status_code=500)
