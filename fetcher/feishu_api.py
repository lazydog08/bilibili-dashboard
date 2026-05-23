from __future__ import annotations

import json
import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from analytics import DEFAULT_TIMEZONE


TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
RECORDS_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"


def _required_env() -> dict[str, str]:
    keys = [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_BASE_APP_TOKEN",
        "FEISHU_TABLE_ID",
    ]
    return {key: os.getenv(key, "") for key in keys}


def is_configured() -> bool:
    return all(_required_env().values())


def _redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _date_value(date_text: str, mode: str = "iso") -> str | int:
    if mode == "ms":
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        local_date = datetime.combine(datetime.fromisoformat(date_text).date(), time.min, tzinfo=tz)
        return int(local_date.timestamp() * 1000)
    return date_text


async def _tenant_token(client: httpx.AsyncClient, app_id: str, app_secret: str) -> str:
    response = await client.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret})
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in {0, "0"}:
        raise RuntimeError(f"Feishu token request failed with code {payload.get('code')}")
    token = payload.get("tenant_access_token")
    if not token:
        raise RuntimeError("Feishu token response did not include tenant_access_token")
    return token


async def _list_records(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, "0"}:
            raise RuntimeError(f"Feishu list records failed with code {payload.get('code')}")
        data = payload.get("data", {})
        records.extend(data.get("items", []) or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token") or ""
        if not page_token:
            break
    return records


def _fields_from_snapshot(snapshot: dict[str, Any], date_format: str) -> dict[str, Any]:
    channel = snapshot.get("channel", {}) if isinstance(snapshot.get("channel"), dict) else {}
    videos = snapshot.get("videos", []) if isinstance(snapshot.get("videos"), list) else []
    date_text = str(snapshot.get("date"))
    return {
        "日期": _date_value(date_text, date_format),
        "总粉丝数": int(channel.get("total_followers") or 0),
        "7日涨粉": int(channel.get("follower_delta_7d") or 0),
        "总播放量": int(channel.get("total_views") or 0),
        "总点赞数": int(channel.get("total_likes") or 0),
        "视频数据JSON": json.dumps(videos, ensure_ascii=False),
    }


async def upsert_daily_summary(snapshot: dict[str, Any], date_format: str = "iso") -> str:
    env = _required_env()
    if not all(env.values()):
        return "Feishu sync skipped: missing configuration."

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await _tenant_token(client, env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])
            headers = {"Authorization": f"Bearer {token}"}
            records_url = RECORDS_URL.format(
                app_token=env["FEISHU_BASE_APP_TOKEN"],
                table_id=env["FEISHU_TABLE_ID"],
            )
            records = await _list_records(client, records_url, headers)
            fields = _fields_from_snapshot(snapshot, date_format)
            target_date = fields["日期"]
            record_id = None
            for record in records:
                record_fields = record.get("fields", {}) if isinstance(record, dict) else {}
                if record_fields.get("日期") == target_date:
                    record_id = record.get("record_id")
                    break

            if record_id:
                response = await client.put(
                    f"{records_url}/{record_id}",
                    headers=headers,
                    json={"fields": fields},
                )
                action = "updated"
            else:
                response = await client.post(
                    records_url,
                    headers=headers,
                    json={"fields": fields},
                )
                action = "created"
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, "0"}:
                raise RuntimeError(f"Feishu upsert failed with code {payload.get('code')}")
            return f"Feishu sync {action} record for {snapshot.get('date')}."
    except Exception as exc:  # noqa: BLE001 - optional integration must never break rendering.
        message = _redact(str(exc), list(env.values()))
        return f"Feishu sync warning: {type(exc).__name__}: {message}"
