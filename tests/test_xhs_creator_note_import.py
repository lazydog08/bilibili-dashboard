from __future__ import annotations

import json

from scripts.import_xhs_creator_notes import normalize_creator_note_rows, update_manual_payload


def test_normalize_opencli_creator_notes_rows() -> None:
    rows = normalize_creator_note_rows(
        [
            {
                "rank": 1,
                "id": "abc123",
                "title": "清闲pro到底好不好？给大家踩踩坑",
                "date": "2026年05月27日 19:50",
                "views": "11822",
                "likes": "687",
                "collects": "282",
                "comments": "47",
                "url": "https://creator.xiaohongshu.com/statistics/note-detail?noteId=abc123",
            }
        ]
    )

    assert rows == [
        {
            "id": "abc123",
            "note_id": "abc123",
            "title": "清闲pro到底好不好？给大家踩踩坑",
            "publish_time": "2026年05月27日 19:50",
            "views": 11822,
            "likes": 687,
            "favorites": 282,
            "comments": 47,
            "shares": None,
            "url": "https://creator.xiaohongshu.com/statistics/note-detail?noteId=abc123",
            "data_source": "小红书创作者后台导入",
            "metric_scope": "当前后台作品列表",
        }
    ]


def test_update_manual_payload_replaces_only_xhs_content_items() -> None:
    payload = {
        "capturedAt": "2026-05-23T20:11:00+08:00",
        "importedAt": "2026-05-23T20:11:00+08:00",
        "platforms": {
            "douyin": {"contentItems": [{"title": "keep douyin"}]},
            "xiaohongshu": {
                "source": "manual_import",
                "fans": 39501,
                "sourceStatus": {
                    "status": "manual",
                    "source": "manual_import",
                    "message": "旧说明",
                },
                "contentItems": [{"title": "旧缓存"}],
            },
        },
    }
    rows = [
        {
            "title": "清闲pro到底好不好？给大家踩踩坑",
            "date": "2026年05月27日 19:50",
            "views": 11822,
            "likes": 687,
            "collects": 282,
            "comments": 47,
        },
        {
            "title": "战争制裁下的俄罗斯，人们过着怎样的生活？",
            "published_at": "2026年04月22日 20:17",
            "views": "120001",
            "likes": "2600",
            "favorites": "970",
            "comments": "58",
            "shares": "166",
        },
    ]

    updated = update_manual_payload(
        payload,
        rows,
        imported_at="2026-06-02T13:00:00+08:00",
        captured_at="2026-06-02T12:59:00+08:00",
    )

    assert updated["platforms"]["douyin"]["contentItems"] == [{"title": "keep douyin"}]
    xhs = updated["platforms"]["xiaohongshu"]
    assert xhs["fans"] == 39501
    assert len(xhs["contentItems"]) == 2
    assert xhs["contentItems"][0]["data_source"] == "小红书创作者后台导入"
    assert xhs["contentItems"][1]["views"] == 120001
    assert xhs["sourceStatus"]["importedAt"] == "2026-06-02T13:00:00+08:00"
    assert xhs["sourceStatus"]["capturedAt"] == "2026-06-02T12:59:00+08:00"
    assert "已导入 2 条小红书创作者后台作品" in xhs["sourceStatus"]["message"]


def test_import_payload_accepts_opencli_json_object_shape() -> None:
    text = json.dumps(
        {
            "rows": [
                {
                    "title": "我做了个离谱的充电器...",
                    "published_at": "2026年03月28日 10:01",
                    "views": "125049",
                    "likes": "4415",
                    "collects": "2439",
                    "comments": "175",
                    "shares": "338",
                    "avg_view_time": "640.9秒",
                }
            ]
        },
        ensure_ascii=False,
    )

    rows = normalize_creator_note_rows(json.loads(text))

    assert rows[0]["favorites"] == 2439
    assert rows[0]["shares"] == 338
    assert rows[0]["avd"] == "640.9秒"
