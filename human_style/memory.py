"""SQLite expression memory. Each operation owns its connection and transaction."""

import hashlib
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

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
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, scope: str, examples: list[Example], source: str, pending=False) -> int:
        # Validate the entire batch before starting a write, including programmatic callers.
        examples = [make_example(e.question, e.answer) for e in examples]
        added = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
        return added

    def list_samples(self, scope: str, status: str = "active", limit: int = 10) -> list[Sample]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,question,answer,source,status,quality,created FROM samples "
                "WHERE scope=? AND status=? ORDER BY id DESC LIMIT ?",
                (scope, status, limit),
            ).fetchall()
        return [Sample(**dict(row)) for row in rows]

    def retrieve(self, scope: str, query: str, limit: int = 4) -> list[Sample]:
        # Consider both established high-quality examples and recent additions.
        with self._connect() as db:
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
            return bool(
                db.execute(
                    "DELETE FROM samples WHERE scope=? AND id=?",
                    (scope, sample_id),
                ).rowcount
            )

    def stats(self, scope: str) -> dict[str, int]:
        with self._connect() as db:
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
            db.execute(
                "INSERT INTO settings VALUES (?, 'collect', ?) "
                "ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value",
                (scope, str(int(enabled))),
            )

    def collection_enabled(self, scope: str, default: bool = True) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE scope=? AND name='collect'",
                (scope,),
            ).fetchone()
        return row[0] == "1" if row else default
