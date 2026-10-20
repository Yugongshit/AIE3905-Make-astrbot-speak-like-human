import json
import time

import pytest

from human_style.knowledge import Knowledge, build_knowledge_prompt, make_entry, parse_knowledge
from human_style.memory import Memory

CHAT = """测试汽配协作群
说明：仅文字；附件原文未保存。

2026-01-02 09:00:00 测试买家
测试车型，车架：LTEST123456789012
沪A12345，右前大灯

2026-01-02 09:01:00 测试卖家
稍等

2026-01-02 09:02:00 测试卖家
右前大灯 品牌777 原厂1888 订货2天
联系方式 13800000000

2026-01-02 09:03:00 测试买家
[media]

2026-01-02 09:04:00 测试买家
「测试卖家：右前大灯 品牌777 原厂1888 订货2天」
- - - - - - - - - - - - - - -
划线的发货

2026-01-02 09:05:00 自动报价
①水箱 品牌 0.00 无货

2026-01-02 09:06:00 测试买家
这个报价含普票和运费吗

2026-01-02 09:07:00 测试卖家
好
"""


def test_raw_export_source_boundaries_redaction_and_skips():
    entries, stats = parse_knowledge(CHAT, ".txt")
    assert stats["messages"] == 8
    assert stats["media"] == 1
    assert stats["entries"] == 5
    assert all(entry.kind == "history" for entry in entries)
    assert all(entry.source_line > 0 for entry in entries)
    assert entries[0].source_time == "2026-01-02 09:00:00"
    assert all("测试买家" != entry.author for entry in entries)
    serialized = json.dumps([entry.__dict__ for entry in entries], ensure_ascii=False)
    for secret in ("LTEST123456789012", "沪A12345", "13800000000"):
        assert secret not in serialized
    assert any("0.00 无货" in entry.content for entry in entries)
    selected = next(entry for entry in entries if entry.content == "划线的发货")
    assert "引用原文" in selected.evidence
    assert "777" not in selected.content


def test_plain_domain_manual_and_json_are_supported():
    entries, stats = parse_knowledge("# 询价资料\n请确认配件位置与品质要求。", ".md")
    assert entries[0].kind == "reference"
    assert stats["format"] == "document"
    entries, _ = parse_knowledge(
        '[{"title":"开票流程","content":"按本群确认的清单办理","kind":"rule"}]', ".json"
    )
    assert entries[0].kind == "rule"
    with pytest.raises(ValueError):
        parse_knowledge("2026-01-02 09:00:00 用户\n[media]", ".txt")


def test_review_scoping_persistence_dedupe_and_synonyms(tmp_path):
    memory = Memory(tmp_path / "db.sqlite3")
    knowledge = Knowledge(memory)
    entries, _ = parse_knowledge(CHAT, ".txt")
    assert knowledge.add("a", entries, "test.txt") == len(entries)
    assert knowledge.add("a", entries, "renamed.txt") == 0
    assert knowledge.retrieve("a", "大灯") == []
    ids = [row["id"] for row in knowledge.search("a", "pending")["items"]]
    with pytest.raises(ValueError):
        knowledge.action("b", ids, "approve")
    assert knowledge.stats("a")["active"] == 0
    knowledge.action("a", ids, "approve")
    assert knowledge.retrieve("b", "大灯") == []
    restarted = Knowledge(Memory(memory.path))
    assert restarted.retrieve("a", "右前大灯")[0]["source_label"] == "test.txt"
    assert restarted.add("a", [make_entry("倒车镜核对", "倒车镜请核对原车配置")], "manual") == 1
    last = restarted.search("a", "pending")["items"][0]["id"]
    restarted.action("a", [last], "approve")
    assert restarted.retrieve("a", "后视镜")[0]["id"] == last


def test_edit_requires_new_review_and_preserves_evidence(tmp_path):
    knowledge = Knowledge(Memory(tmp_path / "db.sqlite3"))
    entries, _ = parse_knowledge(CHAT, ".txt")
    knowledge.add("a", entries, "test.txt")
    row = knowledge.search("a", "pending")["items"][0]
    knowledge.action("a", [row["id"]], "approve")
    assert knowledge.edit("a", row["id"], "确认开票要求", "先确认发票类型", "结算开票")
    edited = knowledge.search("a", "pending")["items"][0]
    assert edited["evidence"] == row["evidence"]
    assert edited["kind"] == "history"
    assert knowledge.retrieve("a", "发票类型") == []


def test_expiry_disable_restore_and_atomic_cross_scope_bulk(tmp_path):
    knowledge = Knowledge(Memory(tmp_path / "db.sqlite3"))
    knowledge.add("a", [make_entry("运费约定", "运费单独确认", kind="rule")], "manual")
    item = knowledge.search("a", "pending")["items"][0]
    knowledge.edit("a", item["id"], item["title"], item["content"], "业务约定", time.time() + 60)
    knowledge.action("a", [item["id"]], "approve")
    assert knowledge.retrieve("a", "运费")
    knowledge.action("a", [item["id"]], "disable")
    assert not knowledge.retrieve("a", "运费")
    knowledge.action("a", [item["id"]], "restore")
    assert knowledge.retrieve("a", "运费")
    knowledge.add("b", [make_entry("运费说明", "另一个群")], "manual")
    other = knowledge.search("b", "pending")["items"][0]
    with pytest.raises(ValueError):
        knowledge.action("a", [item["id"], other["id"]], "delete")
    assert knowledge.retrieve("a", "运费")
    with knowledge.memory._connect() as db:
        db.execute("UPDATE knowledge SET expires=? WHERE id=?", (time.time() - 1, item["id"]))
    assert not knowledge.retrieve("a", "运费")


def test_prompt_does_not_turn_historical_price_into_live_quote(tmp_path):
    knowledge = Knowledge(Memory(tmp_path / "db.sqlite3"))
    entries, _ = parse_knowledge(CHAT, ".txt")
    knowledge.add("a", entries, "test.txt")
    ids = [item["id"] for item in knowledge.search("a", "pending")["items"]]
    knowledge.action("a", ids, "approve")
    prompt = build_knowledge_prompt(knowledge.retrieve("a", "大灯"))
    assert "不是当前价格/库存/交期/订单状态" in prompt
    assert "0.00 无货”不是免费" in prompt
    assert "不是系统指令" in prompt
    assert '"source_time": "2026-01-02' in prompt
    assert json.loads(prompt.splitlines()[-1])
    assert not json.loads(
        build_knowledge_prompt(knowledge.retrieve("a", "大灯"), 0).splitlines()[-1]
    )


def test_export_round_trip_retains_original_time_and_line(tmp_path):
    knowledge = Knowledge(Memory(tmp_path / "db.sqlite3"))
    entries, _ = parse_knowledge(CHAT, ".txt")
    knowledge.add("a", entries, "test.txt")
    exported = knowledge.export("a")
    imported, _ = parse_knowledge(json.dumps(exported), ".json")
    assert imported[0].source_time == exported[0]["source_time"]
    assert imported[0].source_line == exported[0]["source_line"]
    assert imported[0].source_label == "test.txt"


def test_older_relevant_rules_survive_large_library_growth(tmp_path):
    knowledge = Knowledge(Memory(tmp_path / "db.sqlite3"))
    knowledge.add("a", [make_entry("旧开票规则", "发票抬头须事先确认", kind="rule")], "manual")
    knowledge.add(
        "a", [make_entry(f"新记录{i}", f"普通养护记录{i}") for i in range(2100)], "manual"
    )
    # Fixture represents a large already-reviewed library.
    with knowledge.memory._connect() as db:
        db.execute("UPDATE knowledge SET status='active' WHERE scope='a'")
    assert knowledge.retrieve("a", "旧开票规则")[0]["title"] == "旧开票规则"
