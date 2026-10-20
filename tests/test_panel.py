import json

import pytest


@pytest.fixture
def panel(sdk, plugin):
    from fastapi.testclient import TestClient
    from panel_runtime import make_app

    app, headers, context = make_app(plugin)
    for name, group in [("采购协作群", "group-a"), ("供应商群", "group-b")]:
        event = sdk.Event(group=group)
        plugin.learning.memory.remember_group(
            plugin.scope(event), "test-platform", "our-bot", group, name, "test"
        )
    with TestClient(app) as client:
        yield client, headers, context


BASE = "/api/v1/plugins/extensions/astrbot_plugin_human_style/panel"


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def group_ids(panel):
    client, headers, _ = panel
    return [group["id"] for group in data(client.get(BASE + "/state", headers=headers()))["groups"]]


def preview(panel, group, content="问：收到没有？\n答：收到了，多谢你。", owner="test-admin"):
    client, headers, _ = panel
    return data(
        client.post(
            f"{BASE}/groups/{group}/preview-text",
            headers=headers(owner),
            json={"content": content, "format": ".txt"},
        )
    )


def test_dashboard_auth_required_for_read_and_mutation(panel):
    client, headers, _ = panel
    assert client.get(BASE + "/state").status_code == 401
    assert client.post(BASE + "/refresh", json={}).status_code == 401
    assert client.get(BASE + "/template").status_code == 401
    assert client.get(BASE + "/state", headers=headers()).status_code == 200


def test_preview_confirm_idempotence_and_group_isolation(panel):
    client, headers, _ = panel
    a, b = group_ids(panel)
    parsed = preview(panel, a)
    assert parsed["total"] == 1
    assert data(client.get(f"{BASE}/groups/{a}/samples", headers=headers()))["total"] == 0
    assert (
        client.post(
            f"{BASE}/groups/{b}/commit", headers=headers(), json={"token": parsed["token"]}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{BASE}/groups/{a}/commit",
            headers=headers("other-admin"),
            json={"token": parsed["token"]},
        ).status_code
        == 400
    )
    added = data(
        client.post(f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": parsed["token"]})
    )
    assert added == {"added": 1, "duplicates": 0}
    assert (
        client.post(
            f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": parsed["token"]}
        ).status_code
        == 400
    )
    assert data(client.get(f"{BASE}/groups/{b}/samples", headers=headers()))["total"] == 0
    again = preview(panel, a)
    assert (
        data(
            client.post(
                f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": again["token"]}
            )
        )["duplicates"]
        == 1
    )


def test_real_multipart_upload_redaction_and_file_limits(panel):
    client, headers, _ = panel
    a, _ = group_ids(panel)
    url = f"{BASE}/groups/{a}/preview-file"
    response = client.post(
        url,
        headers=headers(),
        files={
            "file": (
                "history.json",
                json.dumps(
                    [{"question": "联系 13812345678", "answer": "收到了，我再确认一下。"}]
                ).encode(),
            )
        },
    )
    assert "13812345678" not in data(response)["examples"][0]["question"]
    assert (
        client.post(url, headers=headers(), files={"file": ("bad.json", b"bad")}).status_code == 400
    )
    assert (
        client.post(
            url, headers=headers(), files={"file": ("big.txt", b"x" * (2 * 1024 * 1024 + 1))}
        ).status_code
        == 400
    )
    assert (
        client.post(url, headers=headers(), files={"file": ("bad.txt", b"\xff")}).status_code == 400
    )


async def test_panel_switches_affect_real_plugin_independently(panel, plugin, sdk):
    client, headers, _ = panel
    groups = data(client.get(BASE + "/state", headers=headers()))["groups"]
    a = next(group["id"] for group in groups if group["group_id"] == "group-a")
    url = f"{BASE}/groups/{a}/settings"
    data(client.post(url, headers=headers(), json={"collect": False}))
    await plugin.collect_messages(sdk.Event("这个价格还有商量空间吗", sender="buyer"))
    await plugin.collect_messages(sdk.Event("可以看看数量再商量一下", sender="supplier"))
    assert plugin.learning.memory.stats(plugin.scope(sdk.Event()))["pending"] == 0
    request = sdk.Request()
    await plugin.style_request(sdk.Event(), request)
    assert "像靠谱" in request.system_prompt
    data(client.post(url, headers=headers(), json={"collect": True, "style": False}))
    first, second = (
        sdk.Event("这个价格还有商量空间吗", sender="buyer"),
        sdk.Event("可以看看数量再商量一下", sender="supplier"),
    )
    await plugin.collect_messages(first)
    await plugin.collect_messages(second)
    assert plugin.learning.memory.stats(plugin.scope(first))["pending"] == 1
    request = sdk.Request(system_prompt="unchanged")
    await plugin.style_request(first, request)
    assert "human_style_expression_v1" not in request.system_prompt
    assert "human_style_auto_parts_knowledge_v1" in request.system_prompt
    data(client.post(url, headers=headers(), json={"knowledge": False}))
    request = sdk.Request(system_prompt="unchanged")
    await plugin.style_request(first, request)
    assert request.system_prompt == "unchanged"


def test_edit_approve_export_search_and_history(panel, plugin):
    client, headers, _ = panel
    a, b = group_ids(panel)
    group = plugin.learning.memory.group(a)
    from human_style.records import Example

    plugin.learning.memory.add(
        group["scope"], [Example("发货了没", "先帮你确认一下")], "live_candidate", pending=True
    )
    sample = data(client.get(f"{BASE}/groups/{a}/samples?status=pending", headers=headers()))[
        "items"
    ][0]
    url = f"{BASE}/groups/{a}/sample"
    assert (
        client.post(
            f"{BASE}/groups/{b}/sample",
            headers=headers(),
            json={"action": "approve", "id": sample["id"]},
        ).status_code
        == 400
    )
    data(
        client.post(
            url,
            headers=headers(),
            json={
                "action": "edit",
                "id": sample["id"],
                "question": "什么时候发货",
                "answer": "你是想确认时间吧，我们先看看安排。",
            },
        )
    )
    data(client.post(url, headers=headers(), json={"action": "approve", "id": sample["id"]}))
    matches = data(client.get(f"{BASE}/groups/{a}/samples?query=确认时间", headers=headers()))
    assert matches["total"] == 1
    exported = client.get(f"{BASE}/groups/{a}/export", headers=headers())
    assert exported.json()[0]["answer"] == "你是想确认时间吧，我们先看看安排。"
    data(
        client.post(url, headers=headers(), json={"action": "rate", "id": sample["id"], "delta": 1})
    )
    data(client.post(url, headers=headers(), json={"action": "delete", "id": sample["id"]}))
    assert data(client.get(f"{BASE}/groups/{a}/samples", headers=headers()))["total"] == 0
    parsed = preview(panel, a)
    data(
        client.post(f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": parsed["token"]})
    )
    group = next(
        g for g in data(client.get(BASE + "/state", headers=headers()))["groups"] if g["id"] == a
    )
    assert group["imports"][0]["added"] == 1


def test_settings_validation_alias_and_preview_expiry(panel, plugin):
    client, headers, _ = panel
    a, _ = group_ids(panel)
    url = f"{BASE}/groups/{a}/settings"
    assert client.post(url, headers=headers(), json={"collect": "false"}).status_code == 400
    assert client.post(url, headers=headers(), json={"unknown": False}).status_code == 400
    data(client.post(url, headers=headers(), json={"alias": "供应商一群"}))
    assert plugin.learning.memory.group(a)["alias"] == "供应商一群"
    parsed = preview(panel, a)
    token = parsed["token"]
    old = plugin.panel._previews[token]
    plugin.panel._previews[token] = (0, *old[1:])
    assert (
        client.post(
            f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": token}
        ).status_code
        == 400
    )


async def test_route_cleanup_and_restart_preserve_controls(panel, plugin):
    client, headers, context = panel
    a, _ = group_ids(panel)
    data(
        client.post(
            f"{BASE}/groups/{a}/settings",
            headers=headers(),
            json={"collect": False, "style": False},
        )
    )
    from human_style.memory import Memory

    restarted = Memory(plugin.learning.memory.path)
    scope = restarted.group(a)["scope"]
    assert not restarted.collection_enabled(scope, True)
    assert not restarted.style_enabled(scope)
    assert len(context.registered_web_apis) == 17
    await plugin.terminate()
    assert context.registered_web_apis == []


async def test_real_plugin_view_discovery_and_authenticated_assets(panel):
    client, headers, _ = panel
    entry = data(
        client.get("/api/v1/plugins/astrbot_plugin_human_style/views/learning", headers=headers())
    )
    page = client.get(entry["content_path"])
    assert page.status_code == 200
    assert "投喂聊天记录" in page.text
    assert "asset_token=" in page.text
    root = entry["content_path"].rsplit("/", 1)[0] + "/"
    assert client.get(root + "app.js").status_code == 200
    assert client.get(root + "style.css").status_code == 200


def test_onebot_sync_and_unsupported_platform_notes(panel, plugin):
    from types import SimpleNamespace

    client, headers, context = panel

    class Client:
        async def call_action(self, action):
            if action == "get_login_info":
                return {"user_id": 12345}
            assert action == "get_group_list"
            return [{"group_id": 67890, "group_name": "同步发现的群"}]

    adapter = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp", id="onebot-test"),
        get_client=lambda: Client(),
    )
    official = SimpleNamespace(meta=lambda: SimpleNamespace(name="qq_official", id="official-test"))
    context.platform_manager.get_insts = lambda: [adapter, official]
    response = data(client.post(BASE + "/refresh", headers=headers(), json={}))
    assert any(
        group["name"] == "同步发现的群" and group["source"] == "platform"
        for group in response["groups"]
    )
    assert any("official-test" in note for note in response["notes"])
    assert any(group["bot_id"] == "12345" for group in response["groups"])


async def test_disabled_plugin_still_discovers_group_but_does_not_learn(sdk, plugin):
    plugin.config = {"enabled": False}
    await plugin.collect_messages(sdk.Event("收到一条新的群消息", group="new-group"))
    groups = plugin.learning.memory.groups()
    assert any(group["group_id"] == "new-group" for group in groups)
    assert plugin.learning.memory.stats(plugin.scope(sdk.Event(group="new-group")))["pending"] == 0


def test_v01_library_and_collection_settings_migrate_without_data_loss(tmp_path):
    import sqlite3

    from human_style.memory import Memory

    path = tmp_path / "old.sqlite3"
    scope = json.dumps(["old-platform", "old-bot", "group", "old-group"])
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE samples(id INTEGER PRIMARY KEY,scope TEXT NOT NULL,question TEXT NOT NULL,
                answer TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,quality INTEGER NOT NULL,
                created REAL NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(scope,fingerprint));
            CREATE TABLE settings(scope TEXT NOT NULL,name TEXT NOT NULL,value TEXT NOT NULL,
                PRIMARY KEY(scope,name));
        """)
        db.execute(
            "INSERT INTO samples VALUES(1,?,'问题','以前的好表达','manual','active',3,1,'old')",
            (scope,),
        )
        db.execute("INSERT INTO settings VALUES(?,'collect','0')", (scope,))
    memory = Memory(path)
    group = memory.groups()[0]
    assert group["group_id"] == "old-group"
    assert group["source"] == "memory"
    assert memory.retrieve(scope, "问题")[0].answer == "以前的好表达"
    assert not memory.collection_enabled(scope, True)
    assert memory.style_enabled(scope)


async def test_raw_chat_upload_review_and_real_model_context(panel, plugin, sdk):
    from test_knowledge import CHAT

    client, headers, _ = panel
    a, b = group_ids(panel)
    parsed = data(
        client.post(
            f"{BASE}/groups/{a}/preview-knowledge-file",
            headers=headers(),
            files={"file": ("parts-chat.txt", CHAT.encode())},
        )
    )
    assert parsed["mode"] == "knowledge"
    assert parsed["details"]["messages"] == 8
    assert parsed["total"] == 5
    assert plugin.learning.knowledge.stats(plugin.learning.memory.group(a)["scope"])["pending"] == 0
    assert (
        client.post(
            f"{BASE}/groups/{b}/commit", headers=headers(), json={"token": parsed["token"]}
        ).status_code
        == 400
    )
    data(
        client.post(f"{BASE}/groups/{a}/commit", headers=headers(), json={"token": parsed["token"]})
    )
    items = data(client.get(f"{BASE}/groups/{a}/knowledge?status=pending", headers=headers()))[
        "items"
    ]
    assert len(items) == 5
    target = plugin.learning.memory.group(a)
    event = sdk.Event("右前大灯多少钱", group=target["group_id"])
    request = sdk.Request(model="deepseekprov4")
    await plugin.style_request(event, request)
    assert "1888" not in request.system_prompt
    assert (
        client.post(
            f"{BASE}/groups/{b}/knowledge-action",
            headers=headers(),
            json={"action": "approve", "ids": [items[0]["id"]]},
        ).status_code
        == 400
    )
    data(
        client.post(
            f"{BASE}/groups/{a}/knowledge-action",
            headers=headers(),
            json={"action": "approve", "ids": [item["id"] for item in items]},
        )
    )
    request = sdk.Request(model="deepseekprov4")
    await plugin.style_request(event, request)
    assert "1888" in request.system_prompt
    assert "不是当前价格/库存/交期/订单状态" in request.system_prompt
    assert request.model == "deepseekprov4"
    assert "LTEST123456789012" not in request.system_prompt
    data(client.post(f"{BASE}/groups/{a}/settings", headers=headers(), json={"knowledge": False}))
    request = sdk.Request()
    await plugin.style_request(event, request)
    assert "human_style_auto_parts_knowledge_v1" not in request.system_prompt


def test_manual_rule_edit_requires_review_and_template_roundtrip(panel):
    client, headers, _ = panel
    a, _ = group_ids(panel)
    data(
        client.post(
            f"{BASE}/groups/{a}/knowledge-action",
            headers=headers(),
            json={
                "action": "add",
                "title": "开票确认",
                "content": "先确认本次需要普票还是专票",
                "category": "结算",
                "kind": "rule",
            },
        )
    )
    item = data(client.get(f"{BASE}/groups/{a}/knowledge?status=pending", headers=headers()))[
        "items"
    ][0]
    data(
        client.post(
            f"{BASE}/groups/{a}/knowledge-action",
            headers=headers(),
            json={"action": "approve", "ids": [item["id"]]},
        )
    )
    data(
        client.post(
            f"{BASE}/groups/{a}/knowledge-action",
            headers=headers(),
            json={
                "action": "edit",
                "id": item["id"],
                "title": item["title"],
                "content": "先确认票种和抬头",
                "category": "结算",
            },
        )
    )
    assert (
        data(client.get(f"{BASE}/groups/{a}/knowledge?status=active", headers=headers()))["total"]
        == 0
    )
    exported = client.get(f"{BASE}/groups/{a}/knowledge-export", headers=headers())
    assert exported.json()[0]["kind"] == "rule"
    template = client.get(BASE + "/template?mode=knowledge", headers=headers())
    assert template.status_code == 200
    from human_style.knowledge import parse_knowledge

    assert parse_knowledge(template.text, ".json")[0]
