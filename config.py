import os
import json
import base64
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class Account:
    jwt: str
    workspace_id: str
    refresh_jwt: str = ""
    label: str = ""


@dataclass
class Settings:
    clickup_token: str = ""
    clickup_jwt: str = ""
    clickup_refresh_jwt: str = ""
    clickup_cookie: str = ""
    api_key: str = ""
    base_url: str = "https://frontdoor-search.clickup-prod.com"
    graphql_path: str = "/graphql/gateway"
    mock: bool = False
    extra_headers_json: str = ""
    anonymous_id: str = ""
    clickup_client_version: str = "4.13.3"
    clickup_locale: str = "en-US"
    clickup_surface: str = "client"
    clickup_tz_offset: str = "-480"
    sd_tab_id: str = ""
    workspace_id: str = ""
    accounts: list = field(default_factory=list)


def _extract_workspace_id(jwt: str) -> str:
    """从 JWT payload 里提取 workspace_id。"""
    try:
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return str(payload.get("workspace_id", ""))
    except Exception:
        return ""


def _parse_accounts() -> list:
    """解析多账号配置。
    优先级：CLICKUP_ACCOUNTS_JSON > CLICKUP_ACCOUNTS > 单账号兼容
    workspace_id 可留空，自动从 JWT payload 提取。
    """
    raw_json = os.getenv("CLICKUP_ACCOUNTS_JSON", "")
    if raw_json:
        try:
            arr = json.loads(raw_json)
            result = []
            for i, a in enumerate(arr):
                jwt = a.get("jwt", "")
                if not jwt:
                    continue
                ws = a.get("workspace_id", "") or _extract_workspace_id(jwt)
                if ws:
                    result.append(Account(
                        jwt=jwt, workspace_id=ws,
                        refresh_jwt=a.get("refresh_jwt", ""),
                        label=a.get("label", f"account-{i+1}"),
                    ))
            if result:
                return result
        except Exception:
            pass

    raw_simple = os.getenv("CLICKUP_ACCOUNTS", "")
    if raw_simple:
        result = []
        for i, item in enumerate(raw_simple.split(",")):
            item = item.strip()
            if not item:
                continue
            parts = item.split("|")
            jwt = parts[0]
            if not jwt:
                continue
            ws = parts[1] if len(parts) > 1 and parts[1] else _extract_workspace_id(jwt)
            if ws:
                result.append(Account(
                    jwt=jwt, workspace_id=ws,
                    refresh_jwt=parts[2] if len(parts) > 2 else "",
                    label=f"account-{i+1}",
                ))
        if result:
            return result

    jwt = os.getenv("CLICKUP_JWT", "")
    if jwt:
        ws = os.getenv("CLICKUP_WORKSPACE_ID", "") or _extract_workspace_id(jwt)
        if ws:
            return [Account(
                jwt=jwt, workspace_id=ws,
                refresh_jwt=os.getenv("CLICKUP_REFRESH_JWT", ""),
                label="default",
            )]
    return []


def load_settings() -> Settings:
    return Settings(
        clickup_token=os.getenv("CLICKUP_TOKEN", ""),
        clickup_jwt=os.getenv("CLICKUP_JWT", ""),
        clickup_refresh_jwt=os.getenv("CLICKUP_REFRESH_JWT", ""),
        clickup_cookie=os.getenv("CLICKUP_COOKIE", ""),
        api_key=os.getenv("API_KEY", ""),
        base_url=os.getenv("CLICKUP_BASE_URL", "https://frontdoor-search.clickup-prod.com"),
        graphql_path=os.getenv("CLICKUP_GRAPHQL_PATH", "/graphql/gateway"),
        mock=os.getenv("CLICKUP_MOCK", "0") == "1",
        extra_headers_json=os.getenv("CLICKUP_EXTRA_HEADERS", ""),
        anonymous_id=os.getenv("CLICKUP_ANONYMOUS_ID", ""),
        clickup_client_version=os.getenv("CLICKUP_CLIENT_VERSION", "4.13.3"),
        clickup_locale=os.getenv("CLICKUP_LOCALE", "en-US"),
        clickup_surface=os.getenv("CLICKUP_SURFACE", "client"),
        clickup_tz_offset=os.getenv("CLICKUP_TZ_OFFSET", "-480"),
        sd_tab_id=os.getenv("CLICKUP_SD_TAB_ID", ""),
        workspace_id=os.getenv("CLICKUP_WORKSPACE_ID", ""),
        accounts=_parse_accounts(),
    )


def build_headers(s: Settings) -> dict:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Accept": "application/graphql-response+json,application/json;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://app.clickup.com",
        "Referer": "https://app.clickup.com/",
        "clickup-client-version": s.clickup_client_version,
        "clickup-locale": s.clickup_locale,
        "clickup-surface": s.clickup_surface,
        "clickup-tz-offset": s.clickup_tz_offset,
    }
    if s.clickup_cookie:
        h["Cookie"] = s.clickup_cookie
    if s.anonymous_id:
        h["anonymous_id"] = s.anonymous_id
    if s.sd_tab_id:
        h["sd-tab-id"] = s.sd_tab_id
    if s.extra_headers_json:
        try:
            h.update(json.loads(s.extra_headers_json))
        except Exception:
            pass
    return h
