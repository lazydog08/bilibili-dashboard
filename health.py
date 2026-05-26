from __future__ import annotations

from typing import Any


def _status_bucket(label: Any) -> str:
    text = str(label or "")
    if text == "成功":
        return "success"
    if text in {"部分可用", "本次失败，显示最近成功"}:
        return "partial"
    if text == "未启用":
        return "disabled"
    return "unavailable"


def _status_class(bucket: str) -> str:
    if bucket == "success":
        return "is-positive"
    if bucket == "partial":
        return "is-warning"
    return "is-unavailable"


def _platform_quality(platform_cards: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"success": 0, "partial": 0, "disabled": 0, "unavailable": 0}
    problems: list[str] = []
    for card in platform_cards:
        bucket = _status_bucket(card.get("status_label"))
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket != "success":
            name = str(card.get("name") or "未知平台")
            status = str(card.get("status_label") or "暂不可用")
            problems.append(f"{name}：{status}")

    if counts["unavailable"]:
        bucket = "unavailable"
        value = f"{counts['success']} 成功 / {counts['partial']} 部分 / {counts['unavailable']} 异常"
    elif counts["partial"]:
        bucket = "partial"
        value = f"{counts['success']} 成功 / {counts['partial']} 部分可用"
    else:
        bucket = "success"
        value = f"{counts['success']} 个平台正常"

    return {
        "label": "平台数据质量",
        "value": value,
        "meta": "；".join(problems[:2]) if problems else "三平台数据源均返回可展示结果",
        "class": _status_class(bucket),
    }


def build_operational_status(
    platform_cards: list[dict[str, Any]],
    *,
    next_update_label: str,
    update_interval_minutes: int | None,
    page_refresh_seconds: int,
) -> dict[str, Any]:
    interval = int(update_interval_minutes or 0)
    refresh = int(page_refresh_seconds or 0)
    cadence_value = f"{interval} 分钟" if interval else "按固定时刻"
    refresh_meta = f"页面自动刷新 {refresh // 60} 分钟" if refresh >= 60 else "页面自动刷新关闭"

    return {
        "cards": [
            {
                "label": "NAS 更新节奏",
                "value": cadence_value,
                "meta": next_update_label or "下次更新时间未配置",
                "class": "is-positive",
            },
            _platform_quality(platform_cards),
            {
                "label": "发布方式",
                "value": "静态页面",
                "meta": f"GitHub Pages 部署 dashboard/output；{refresh_meta}",
                "class": "is-positive",
            },
        ]
    }
