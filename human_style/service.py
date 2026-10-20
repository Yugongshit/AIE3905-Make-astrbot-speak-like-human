"""The ingestion entry point can also be used by future UIs or adapters."""

import threading
import time
from collections import OrderedDict
from pathlib import Path

from .memory import Memory
from .records import make_example, read_records


class LearningService:
    def __init__(self, data_dir: Path):
        self.inbox = data_dir / "inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(data_dir / "expression_memory.sqlite3")
        self._previous: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        self._lock = threading.Lock()

    def import_file(self, scope: str, filename: str) -> int:
        # A filename in the dedicated inbox, never an arbitrary path or URL.
        if not filename or Path(filename).name != filename or "\\" in filename:
            raise ValueError("只接受收件目录内的文件名，不能使用路径或网址")
        path = (self.inbox / filename).resolve()
        if path.parent != self.inbox.resolve():
            raise ValueError("文件不能指向收件目录外")
        return self.import_attachment(scope, path, source="import")

    def import_attachment(
        self,
        scope: str,
        path: Path,
        source="upload",
        suffix: str | None = None,
    ) -> int:
        return self.memory.add(scope, read_records(path, suffix), source)

    def add_example(self, scope: str, question: str, answer: str, source="manual") -> int:
        return self.memory.add(scope, [make_example(question, answer)], source)

    def collect(self, scope: str, sender: str, text: str, default=True) -> int:
        """Adjacent different-speaker messages are candidates, never trusted answers."""
        if not self.memory.collection_enabled(scope, default):
            return 0
        now = time.monotonic()
        if not sender or not 4 <= len(text.strip()) <= 1000 or text.startswith("/"):
            return 0
        with self._lock:
            previous = self._previous.pop(scope, None)
            self._previous[scope] = (sender, text, now)
            while len(self._previous) > 256:
                self._previous.popitem(last=False)
        if not previous or previous[0] == sender or now - previous[2] > 180:
            return 0
        try:
            example = make_example(previous[1], text)
        except ValueError:
            return 0
        return self.memory.add(scope, [example], "live_candidate", pending=True)

    def set_collection(self, scope: str, enabled: bool):
        self.memory.set_collection(scope, enabled)
        with self._lock:
            self._previous.pop(scope, None)
