"""SQLite expression memory. Each operation owns its connection and transaction."""

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .identity import QQ_OFFICIAL, parse_scope, qq_account, scope_key
from .records import Example, make_example


@dataclass(frozen=True)
class Sample:
    id: int
    question: str
    answer: str
    source: str
    status: str
    quality: int
    created: float


def tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
        if len(phrase) == 1:
            words.add(phrase)
    return words


class Memory:
    def __init__(self, path: Path, max_pending: int = 200):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_pending = max_pending
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY,
                    scope TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','pending','disabled')),
                    quality INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    fingerprint TEXT NOT NULL,
                    UNIQUE(scope, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS samples_scope ON samples(scope, status);
                CREATE TABLE IF NOT EXISTS settings (
                    scope TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY(scope, name)
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY,
                    scope TEXT NOT NULL UNIQUE,
                    platform_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    alias TEXT NOT NULL DEFAULT '',
                    platform_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    last_seen REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS scope_aliases (
                    old_scope TEXT PRIMARY KEY,
                    target_scope TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY,
                    scope TEXT NOT NULL,
                    label TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    added INTEGER NOT NULL,
                    created REAL NOT NULL
                );
            """)
            # Existing v0.1 learning libraries remain selectable in the panel.
            scopes = db.execute(
                "SELECT DISTINCT scope FROM samples UNION SELECT scope FROM settings"
            )
            for row in scopes.fetchall():
                try:
                    platform, bot, kind, group = json.loads(row[0])
                    if kind == "group":
                        db.execute(
                            "INSERT OR IGNORE INTO groups "
                            "(scope,platform_id,bot_id,group_id,name,platform_name,source) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (row[0], platform, bot, group, group, platform, "memory"),
                        )
                except (ValueError, TypeError):
                    continue

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _resolve_scope(self, db, scope: str) -> str:
        seen = set()
        while scope not in seen:
            seen.add(scope)
            row = db.execute(
                "SELECT target_scope FROM scope_aliases WHERE old_scope=?", (scope,)
            ).fetchone()
            if not row:
                return scope
            scope = row[0]
        raise ValueError("群标识映射异常，请检查学习库备份")

    def resolve_scope(self, scope: str) -> str:
        with self._connect() as db:
            return self._resolve_scope(db, scope)

    @staticmethod
    def _merge_status(left, right):
        # A migration must not silently approve or reactivate a disabled record.
        return max((left, right), key={"active": 0, "pending": 1, "disabled": 2}.__getitem__)

    def reconcile_groups(self, official_accounts: dict[str, str] | None = None) -> dict:
        """Atomically move old scopes, including their libraries, to stable identities."""
        official_accounts = official_accounts or {}
        result = {"merged": 0, "moved": 0, "backup": ""}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            children = [
                name for name in ("samples", "settings", "imports", "knowledge") if name in tables
            ]
            groups = {row["scope"]: dict(row) for row in db.execute("SELECT * FROM groups")}
            scopes = set(groups)
            for table in children:
                scopes.update(row[0] for row in db.execute(f"SELECT DISTINCT scope FROM {table}"))
            plan = {}
            for old in scopes:
                parts = parse_scope(old)
                if parts is None:
                    continue
                platform, bot, kind, target = parts
                row = groups.get(old, {})
                if (
                    platform in official_accounts
                    or row.get("platform_name") in QQ_OFFICIAL
                    or bot in ("qq_official", "unknown_selfid")
                    or bot.startswith(("qq-app:", "qq-platform:"))
                ):
                    # A known application account remains isolated if the platform
                    # instance is later reconfigured to a different application.
                    if not bot.startswith("qq-app:"):
                        bot = official_accounts.get(platform) or qq_account(platform)
                canonical = self._resolve_scope(db, scope_key(platform, bot, kind, target))
                if old != canonical:
                    plan[old] = canonical
            if not plan:
                return result
            # Back up the complete pre-migration database while the reserved write
            # lock prevents concurrent writers; the separate connection can read it.
            backup = self.path.with_name(
                f"{self.path.stem}.before-group-fix-{time.time_ns()}.sqlite3"
            )
            with self._connect() as original:
                destination = sqlite3.connect(backup)
                try:
                    original.backup(destination)
                finally:
                    destination.close()
            result["backup"] = backup.name
            buckets = {}
            for old, target in plan.items():
                buckets.setdefault(target, set()).add(old)
            for target, old_scopes in buckets.items():
                platform, bot, kind, group_id = parse_scope(target)
                related = [row for key, row in groups.items() if key in old_scopes or key == target]
                if related:
                    # Keep the largest existing ID so SQLite never reuses an ID
                    # removed from this directory by the merge.
                    keep = max(related, key=lambda row: row["id"])
                    latest = sorted(
                        related, key=lambda row: (row["last_seen"], row["id"]), reverse=True
                    )
                    alias = next((row["alias"] for row in latest if row["alias"]), "")
                    name = next(
                        (row["name"] for row in latest if row["name"] and row["name"] != group_id),
                        group_id,
                    )
                    platform_name = next(
                        (
                            row["platform_name"]
                            for row in latest
                            if row["platform_name"] in QQ_OFFICIAL
                        ),
                        keep["platform_name"],
                    )
                    source = next(
                        (row["source"] for row in latest if row["source"] == "message"),
                        keep["source"],
                    )
                    for row in related:
                        if row["id"] != keep["id"]:
                            db.execute("DELETE FROM groups WHERE id=?", (row["id"],))
                    db.execute(
                        "UPDATE groups SET scope=?,platform_id=?,bot_id=?,group_id=?,name=?,alias=?,platform_name=?,source=?,last_seen=? WHERE id=?",
                        (
                            target,
                            platform,
                            bot,
                            group_id,
                            name,
                            alias,
                            platform_name,
                            source,
                            max(row["last_seen"] for row in related),
                            keep["id"],
                        ),
                    )
                    result["merged"] += len(related) - 1
                elif kind == "group":
                    db.execute(
                        "INSERT INTO groups(scope,platform_id,bot_id,group_id,name,platform_name,source) VALUES (?,?,?,?,?,?,?)",
                        (
                            target,
                            platform,
                            bot,
                            group_id,
                            group_id,
                            "qq_official" if platform in official_accounts else platform,
                            "memory",
                        ),
                    )
                for old in old_scopes:
                    for table in ("samples", "knowledge"):
                        if table not in tables:
                            continue
                        for row in db.execute(
                            f"SELECT * FROM {table} WHERE scope=?", (old,)
                        ).fetchall():
                            collision = db.execute(
                                f"SELECT * FROM {table} WHERE scope=? AND fingerprint=?",
                                (target, row["fingerprint"]),
                            ).fetchone()
                            if (
                                collision
                                and table == "samples"
                                and (row["question"], row["answer"])
                                == (collision["question"], collision["answer"])
                            ):
                                quality = min(row["quality"], collision["quality"])
                                status = (
                                    "disabled"
                                    if quality <= -2
                                    else self._merge_status(row["status"], collision["status"])
                                )
                                db.execute(
                                    "UPDATE samples SET quality=?,status=?,created=MIN(created,?) WHERE id=?",
                                    (quality, status, row["created"], collision["id"]),
                                )
                                db.execute("DELETE FROM samples WHERE id=?", (row["id"],))
                            else:
                                # Knowledge may have been edited without changing its
                                # fingerprint. Retain every version and its evidence.
                                fingerprint = row["fingerprint"]
                                if collision:
                                    fingerprint = hashlib.sha256(
                                        f"{table}:{old}:{row['id']}:{fingerprint}".encode()
                                    ).hexdigest()
                                db.execute(
                                    f"UPDATE {table} SET scope=?,fingerprint=? WHERE id=?",
                                    (target, fingerprint, row["id"]),
                                )
                    for row in db.execute(
                        "SELECT * FROM settings WHERE scope=?", (old,)
                    ).fetchall():
                        db.execute(
                            "INSERT INTO settings(scope,name,value) VALUES (?,?,?) ON CONFLICT(scope,name) DO UPDATE SET value=CASE WHEN settings.value='0' OR excluded.value='0' THEN '0' ELSE settings.value END",
                            (target, row["name"], row["value"]),
                        )
                    db.execute("DELETE FROM settings WHERE scope=?", (old,))
                    db.execute("UPDATE imports SET scope=? WHERE scope=?", (target, old))
                    db.execute(
                        "UPDATE scope_aliases SET target_scope=? WHERE target_scope=?",
                        (target, old),
                    )
                    db.execute(
                        "INSERT INTO scope_aliases VALUES (?,?) ON CONFLICT(old_scope) DO UPDATE SET target_scope=excluded.target_scope",
                        (old, target),
                    )
                    result["moved"] += 1
        return result

    def add(
        self,
        scope: str,
        examples: list[Example],
        source: str,
        pending=False,
        import_label: str | None = None,
    ) -> int:
        # Validate the entire batch before starting a write, including programmatic callers.
        examples = [make_example(e.question, e.answer) for e in examples]
        added = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            for example in examples:
                fingerprint = hashlib.sha256(
                    (example.question + "\0" + example.answer).encode()
                ).hexdigest()
                cursor = db.execute(
                    "INSERT OR IGNORE INTO samples "
                    "(scope,question,answer,source,status,created,fingerprint) VALUES (?,?,?,?,?,?,?)",
                    (
                        scope,
                        example.question,
                        example.answer,
                        source,
                        "pending" if pending else "active",
                        time.time(),
                        fingerprint,
                    ),
                )
                added += cursor.rowcount
            # Bound the live collection queue per group; never evict approved examples.
            db.execute(
                "DELETE FROM samples WHERE scope=? AND status='pending' AND id NOT IN "
                "(SELECT id FROM samples WHERE scope=? AND status='pending' "
                "ORDER BY id DESC LIMIT ?)",
                (scope, scope, self.max_pending),
            )
            if import_label is not None:
                db.execute(
                    "INSERT INTO imports(scope,label,total,added,created) VALUES (?,?,?,?,?)",
                    (scope, import_label[:120], len(examples), added, time.time()),
                )
        return added

    def list_samples(self, scope: str, status: str = "active", limit: int = 10) -> list[Sample]:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            rows = db.execute(
                "SELECT id,question,answer,source,status,quality,created FROM samples "
                "WHERE scope=? AND status=? ORDER BY id DESC LIMIT ?",
                (scope, status, limit),
            ).fetchall()
        return [Sample(**dict(row)) for row in rows]

    def retrieve(self, scope: str, query: str, limit: int = 4) -> list[Sample]:
        # Consider both established high-quality examples and recent additions.
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            rows = db.execute(
                "SELECT id,question,answer,source,status,quality,created FROM samples "
                "WHERE scope=? AND status='active' ORDER BY quality DESC,id DESC LIMIT 500",
                (scope,),
            ).fetchall()
            recent = db.execute(
                "SELECT id,question,answer,source,status,quality,created FROM samples "
                "WHERE scope=? AND status='active' ORDER BY id DESC LIMIT 500",
                (scope,),
            ).fetchall()
        samples = {row["id"]: Sample(**dict(row)) for row in [*rows, *recent]}
        query_tokens = tokens(query)

        def rank(sample: Sample):
            sample_tokens = tokens(sample.question)
            overlap = len(query_tokens & sample_tokens)
            relevance = overlap / math.sqrt(max(1, len(query_tokens) * len(sample_tokens)))
            return (relevance * 5 + sample.quality * 0.08, sample.id)

        return sorted(samples.values(), key=rank, reverse=True)[:limit]

    def approve(self, scope: str, sample_id: int) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            return bool(
                db.execute(
                    "UPDATE samples SET status='active' WHERE scope=? AND id=? AND status='pending'",
                    (scope, sample_id),
                ).rowcount
            )

    def rate(self, scope: str, sample_id: int, delta: int) -> bool:
        if delta not in (-1, 1):
            raise ValueError("评分只能为 +1 或 -1")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            return bool(
                db.execute(
                    "UPDATE samples SET quality=MIN(10,MAX(-2,quality+?)), "
                    "status=CASE WHEN quality+?<=-2 THEN 'disabled' ELSE 'active' END "
                    "WHERE scope=? AND id=? AND status='active'",
                    (delta, delta, scope, sample_id),
                ).rowcount
            )

    def delete(self, scope: str, sample_id: int) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            return bool(
                db.execute(
                    "DELETE FROM samples WHERE scope=? AND id=?",
                    (scope, sample_id),
                ).rowcount
            )

    def stats(self, scope: str) -> dict[str, int]:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            rows = db.execute(
                "SELECT status,COUNT(*) AS n FROM samples WHERE scope=? GROUP BY status",
                (scope,),
            ).fetchall()
        return {
            **dict.fromkeys(("active", "pending", "disabled"), 0),
            **{row["status"]: row["n"] for row in rows},
        }

    def set_collection(self, scope: str, enabled: bool):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            db.execute(
                "INSERT INTO settings VALUES (?, 'collect', ?) "
                "ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value",
                (scope, str(int(enabled))),
            )

    def collection_enabled(self, scope: str, default: bool = True) -> bool:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            row = db.execute(
                "SELECT value FROM settings WHERE scope=? AND name='collect'",
                (scope,),
            ).fetchone()
        return row[0] == "1" if row else default

    def remember_group(
        self,
        scope: str,
        platform_id: str,
        bot_id: str,
        group_id: str,
        name: str = "",
        platform_name: str = "",
        source: str = "message",
    ) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            db.execute(
                "INSERT INTO groups(scope,platform_id,bot_id,group_id,name,platform_name,source,last_seen) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                "name=CASE WHEN ?!='' THEN excluded.name ELSE groups.name END, "
                "platform_name=excluded.platform_name, "
                "source=CASE WHEN excluded.source='history' AND groups.source!='history' "
                "THEN groups.source ELSE excluded.source END, "
                "last_seen=CASE WHEN excluded.last_seen>0 THEN excluded.last_seen ELSE groups.last_seen END",
                (
                    scope,
                    platform_id,
                    bot_id,
                    group_id,
                    name or group_id,
                    platform_name or platform_id,
                    source,
                    time.time() if source == "message" else 0,
                    name,
                ),
            )
            return db.execute("SELECT id FROM groups WHERE scope=?", (scope,)).fetchone()[0]

    def groups(self) -> list[dict]:
        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM groups ORDER BY last_seen DESC,id DESC")
            ]

    def group(self, group_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
        return dict(row) if row else None

    def rename_group(self, scope: str, alias: str):
        if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= 80:
            raise ValueError("群备注请输入 1–80 字符")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            db.execute("UPDATE groups SET alias=? WHERE scope=?", (alias.strip(), scope))

    def set_style(self, scope: str, enabled: bool):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            db.execute(
                "INSERT INTO settings VALUES (?, 'style', ?) "
                "ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value",
                (scope, str(int(enabled))),
            )

    def style_enabled(self, scope: str) -> bool:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            row = db.execute(
                "SELECT value FROM settings WHERE scope=? AND name='style'", (scope,)
            ).fetchone()
        return row[0] == "1" if row else True

    def set_knowledge(self, scope: str, enabled: bool):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            db.execute(
                "INSERT INTO settings VALUES (?, 'knowledge', ?) "
                "ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value",
                (scope, str(int(enabled))),
            )

    def knowledge_enabled(self, scope: str) -> bool:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            row = db.execute(
                "SELECT value FROM settings WHERE scope=? AND name='knowledge'", (scope,)
            ).fetchone()
        return row[0] == "1" if row else True

    def search_samples(self, scope: str, status="active", query="", page=1, size=20) -> dict:
        if status not in ("active", "pending", "disabled"):
            raise ValueError("无效的样例状态")
        page, size = max(1, page), max(1, min(50, size))
        # instr treats % and _ literally, unlike an unescaped LIKE search.
        where = "scope=? AND status=? AND (instr(question,?)>0 OR instr(answer,?)>0)"
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            args = (scope, status, query, query)
            total = db.execute(f"SELECT COUNT(*) FROM samples WHERE {where}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT id,question,answer,source,status,quality,created FROM samples WHERE {where} "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (*args, size, (page - 1) * size),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "size": size}

    def edit(self, scope: str, sample_id: int, question: str, answer: str) -> bool:
        example = make_example(question, answer)
        fingerprint = hashlib.sha256(
            (example.question + "\0" + example.answer).encode()
        ).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._resolve_scope(db, scope)
            try:
                return bool(
                    db.execute(
                        "UPDATE samples SET question=?,answer=?,fingerprint=? WHERE scope=? AND id=?",
                        (example.question, example.answer, fingerprint, scope, sample_id),
                    ).rowcount
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("相同的问答已经存在") from exc

    def import_history(self, scope: str) -> list[dict]:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,label,total,added,created FROM imports WHERE scope=? ORDER BY id DESC LIMIT 20",
                    (scope,),
                )
            ]

    def export(self, scope: str) -> list[dict]:
        with self._connect() as db:
            scope = self._resolve_scope(db, scope)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT question,answer FROM samples WHERE scope=? AND status='active' ORDER BY id",
                    (scope,),
                )
            ]
