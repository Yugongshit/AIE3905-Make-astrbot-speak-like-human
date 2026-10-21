"""Regressions for duplicate QQ groups and lossless identity migration."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from human_style.identity import qq_account, scope_key
from human_style.knowledge import make_entry
from human_style.records import Example
from human_style.service import LearningService


def official_context(platform="default_1900000000", appid="1900000000"):
    adapter = SimpleNamespace(
        meta=lambda: SimpleNamespace(id=platform, name="qq_official"), appid=appid
    )
    return SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: [adapter]))


def qq_event(sdk, self_id, platform="default_1900000000"):
    event = sdk.Event("机盖什么时候发货", platform=platform)
    event.platform_meta.name = "qq_official"
    event.message_obj.self_id = self_id
    return event


async def test_qq_mentions_and_unmentioned_messages_use_same_group_and_library(plugin, sdk):
    plugin.context = official_context()
    plain = qq_event(sdk, "qq_official")
    mention = qq_event(sdk, "mention-user-123")
    assert plugin.scope(plain) == plugin.scope(mention)
    assert json.loads(plugin.scope(plain))[1] == "qq-app:1900000000"
    plugin.learning.add_example(
        plugin.scope(plain), "机盖什么时候发货", "你着急用吧，先确认一下时间。"
    )
    plugin.learning.memory.set_style(plugin.scope(plain), False)
    await plugin.collect_messages(plain)
    await plugin.collect_messages(mention)
    assert len(plugin.learning.memory.groups()) == 1
    assert plugin.learning.memory.groups()[0]["bot_id"] == "qq-app:1900000000"
    request = sdk.Request(system_prompt="base")
    await plugin.style_request(mention, request)
    assert "你着急用吧" not in request.system_prompt
    plugin.learning.memory.set_style(plugin.scope(plain), True)
    request = sdk.Request()
    await plugin.style_request(mention, request)
    assert "你着急用吧" in request.system_prompt


def test_full_migration_preserves_libraries_and_backs_up_before_merging(tmp_path):
    service = LearningService(tmp_path)
    memory, knowledge = service.memory, service.knowledge
    platform, group = "default_1900000000", "ABCDEF123"
    old_a = scope_key(platform, "qq_official", "group", group)
    old_b = scope_key(platform, "mention-user-123", "group", group)
    canonical = scope_key(platform, qq_account(platform), "group", group)
    for scope, bot in [(old_a, "qq_official"), (old_b, "mention-user-123")]:
        memory.remember_group(scope, platform, bot, group, "同一个汽配群", "qq_official")
    memory.rename_group(old_a, "工作群备注")
    memory.add(old_a, [Example("问题甲", "原来的好表达甲")], "panel", import_label="style-a.json")
    memory.add(old_b, [Example("问题乙", "原来的好表达乙")], "panel", import_label="style-b.json")
    for scope, flag in [(old_a, False), (old_b, True)]:
        memory.set_collection(scope, flag)
        memory.set_style(scope, flag)
        memory.set_knowledge(scope, flag)
    knowledge.add(old_a, [make_entry("甲术语", "甲术语解释")], "a.txt")
    knowledge.add(old_b, [make_entry("乙流程", "乙流程解释")], "b.txt")
    item = knowledge.search(old_b, "pending")["items"][0]
    knowledge.action(old_b, [item["id"]], "approve")
    result = memory.reconcile_groups({platform: qq_account(platform)})
    assert result["merged"] == 1
    assert result["moved"] == 2
    backup = tmp_path / result["backup"]
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 2
        assert {r[0] for r in db.execute("SELECT DISTINCT scope FROM samples")} == {old_a, old_b}
    groups = memory.groups()
    assert len(groups) == 1
    assert groups[0]["scope"] == canonical
    assert groups[0]["alias"] == "工作群备注"
    assert memory.stats(canonical)["active"] == 2
    assert len(memory.import_history(canonical)) == 2
    assert knowledge.stats(canonical) == {"active": 1, "pending": 1, "disabled": 0}
    assert not memory.collection_enabled(canonical, True)
    assert not memory.style_enabled(canonical)
    assert not memory.knowledge_enabled(canonical)
    # A late callback holding an old scope must not recreate a split library.
    memory.add(old_a, [Example("新问题", "新的好表达")], "manual")
    knowledge.add(old_b, [make_entry("新术语", "新术语解释")], "manual")
    assert memory.stats(canonical)["active"] == 3
    assert memory.stats(old_b)["active"] == 3
    assert knowledge.stats(old_a)["pending"] == 2
    assert memory.search_samples(old_a)["total"] == 3
    assert knowledge.search(old_b, "pending")["total"] == 2
    memory.remember_group(old_a, platform, "qq_official", group, platform_name="qq_official")
    assert len(memory.groups()) == 1
    assert memory.reconcile_groups({platform: qq_account(platform)})["moved"] == 0
    restarted = LearningService(tmp_path)
    assert len(restarted.memory.groups()) == 1
    assert restarted.memory.stats(canonical)["active"] == 3
    assert len(list(tmp_path.glob("*.before-group-fix-*.sqlite3"))) == 1


def test_merge_duplicate_samples_conservatively_and_keep_edited_knowledge(tmp_path):
    service = LearningService(tmp_path)
    a = scope_key("default_1900000000", "qq_official", "group", "group")
    b = scope_key("default_1900000000", "real-id", "group", "group")
    for scope in [a, b]:
        service.memory.add(
            scope, [Example("共同的问题", "同一条表达")], "manual", pending=scope == b
        )
        service.knowledge.add(scope, [make_entry("同一术语", "最初的解释")], "manual")
    entry = service.knowledge.search(b, "pending")["items"][0]
    service.knowledge.edit(b, entry["id"], "修改后的术语", "保留管理员修改的解释", "部件区别")
    service.memory.reconcile_groups({"default_1900000000": "qq-app:1900000000"})
    target = scope_key("default_1900000000", "qq-app:1900000000", "group", "group")
    assert service.memory.stats(target) == {"active": 0, "pending": 1, "disabled": 0}
    items = service.knowledge.search(target, "pending")["items"]
    assert {item["content"] for item in items} == {"最初的解释", "保留管理员修改的解释"}


def test_serialization_duplicates_merge_without_merging_distinct_bot_accounts(tmp_path):
    memory = LearningService(tmp_path).memory
    a = json.dumps(["onebot", 123, "group", 456], separators=(",", ":"))
    b = scope_key("onebot", "123", "group", "456")
    other = scope_key("onebot", "789", "group", "456")
    another_platform = scope_key("another-onebot", "123", "group", "456")
    for scope, platform, bot in [
        (a, "onebot", "123"),
        (b, "onebot", "123"),
        (other, "onebot", "789"),
        (another_platform, "another-onebot", "123"),
    ]:
        memory.remember_group(scope, platform, bot, "456", "相同群名", "aiocqhttp")
        memory.add(scope, [Example("你好", "你好呀")], "manual")
    assert memory.reconcile_groups()["merged"] == 1
    assert len(memory.groups()) == 3
    assert {row["scope"] for row in memory.groups()} == {b, other, another_platform}
    assert memory.stats(b)["active"] == 1


def test_qq_applications_stay_isolated_after_adapter_reconfiguration(plugin, sdk):
    plugin.context = official_context(platform="custom-qq", appid="old-app")
    old = qq_event(sdk, "qq_official", platform="custom-qq")
    old_scope = plugin.scope(old)
    plugin.learning.add_example(old_scope, "问题", "旧账号内容")
    plugin.learning.memory.remember_group(
        old_scope, "custom-qq", "qq-app:old-app", "group-a", platform_name="qq_official"
    )
    plugin.context = official_context(platform="custom-qq", appid="new-app")
    new = qq_event(sdk, "qq_official", platform="custom-qq")
    new_scope = plugin.scope(new)
    assert old_scope != new_scope
    assert plugin.learning.memory.stats(old_scope)["active"] == 1
    assert plugin.learning.memory.stats(new_scope)["active"] == 0
    plugin.reconcile_groups()
    assert plugin.learning.memory.stats(old_scope)["active"] == 1


def test_failed_migration_rolls_back_every_table_and_retry_is_safe(tmp_path):
    service = LearningService(tmp_path)
    old = scope_key("default_1900000000", "qq_official", "group", "group")
    service.memory.remember_group(
        old, "default_1900000000", "qq_official", "group", platform_name="qq_official"
    )
    service.memory.add(old, [Example("问题", "原来的表达")], "manual")
    service.knowledge.add(old, [make_entry("术语", "术语解释")], "manual")
    with service.memory._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_identity_migration BEFORE UPDATE OF scope ON knowledge BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.memory.reconcile_groups()
    assert service.memory.groups()[0]["scope"] == old
    assert service.memory.stats(old)["active"] == 1
    assert service.knowledge.stats(old)["pending"] == 1
    with service.memory._connect() as db:
        db.execute("DROP TRIGGER fail_identity_migration")
    assert service.memory.reconcile_groups()["moved"] == 1
