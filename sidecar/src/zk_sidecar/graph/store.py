"""The graph's own files in .zk/graph: an extraction cache, and the last build.

The cache is keyed by extractor and chunk text, so each chunk is read once, whatever its note does.
"""
import json
import os
import sqlite3
import threading
from pathlib import Path


class GraphStore:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(folder / 'cache.db', check_same_thread=False)
        self._db.execute('CREATE TABLE IF NOT EXISTS extract (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self._db.commit()
        self._lock = threading.Lock()

    def cached(self, keys: list[str]) -> dict[str, list]:
        out: dict[str, list] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                q = f'SELECT key, value FROM extract WHERE key IN ({",".join("?" * len(part))})'
                out.update((k, json.loads(v)) for k, v in self._db.execute(q, part))
        return out

    def cache(self, items: dict[str, list]) -> None:
        with self._lock:
            self._db.executemany('INSERT OR REPLACE INTO extract VALUES (?, ?)',
                                 [(k, json.dumps(v)) for k, v in items.items()])
            self._db.commit()

    @property
    def _state_file(self) -> Path:
        return self.folder / 'build.json'

    def state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        tmp = self._state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(state))
        os.replace(tmp, self._state_file)
