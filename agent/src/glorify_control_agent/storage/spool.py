"""Single-process durable queue. DELETE journal bounds disk without an unbounded WAL."""

import json
import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock

log = logging.getLogger(__name__)
MAX_PAYLOAD = 240 * 1024
MAX_PG_REPORT = 1024 * 1024


class Spool:
    def __init__(self, path, max_bytes=64 * 1024 * 1024, max_items=10000, retention=7 * 86400):
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        self.max_bytes, self.max_items, self.retention = max_bytes, max_items, retention
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={max_bytes // page_size}")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                dead INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS queue_due ON queue(dead, due);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
        """)
        self.prune()

    def _prune(self, incoming=0):
        now = time.time()
        before = self.db.total_changes
        self.db.execute("DELETE FROM queue WHERE created < ?", (now - self.retention,))
        # Operator pauses are configuration, not expiring observation state.
        self.db.execute("DELETE FROM state WHERE updated < ? AND key != 'control'", (now - self.retention,))
        # Reserve half the physical DB limit for indexes, state and fragmentation.
        count, size = self.db.execute(
            "SELECT count(*), coalesce(sum(length(CAST(payload AS BLOB))),0) FROM queue"
        ).fetchone()
        while count > self.max_items - int(incoming > 0) or size + incoming > self.max_bytes // 2:
            row = self.db.execute(
                "SELECT id, length(CAST(payload AS BLOB)) FROM queue ORDER BY created,id LIMIT 1"
            ).fetchone()
            if not row:
                break
            self.db.execute("DELETE FROM queue WHERE id=?", (row[0],))
            count -= 1
            size -= row[1]
        removed = self.db.total_changes - before
        if removed:
            log.warning("spool_retention_or_capacity_eviction count=%d", removed)
        self.db.execute(
            """DELETE FROM state WHERE key IN
            (SELECT key FROM state WHERE key != 'control' ORDER BY updated DESC LIMIT -1 OFFSET ?)""",
            (self.max_items,),
        )

    def prune(self):
        with self.lock, self.db:
            self._prune()
            self.db.execute("PRAGMA incremental_vacuum(128)")

    def enqueue(self, route, payload, state=None):
        size = len(payload.encode("utf-8"))
        limit = MAX_PG_REPORT if route == "pg-statements" else MAX_PAYLOAD
        if size > limit or size > self.max_bytes // 2:
            raise ValueError("Payload exceeds durable queue limit")
        if route not in {"metrics", "events", "pg-statements"}:
            raise ValueError("Unknown route")
        with self.lock, self.db:
            self._prune(size)
            now = time.time()
            self.db.execute(
                "INSERT INTO queue(route,payload,created,due) VALUES(?,?,?,?)", (route, payload, now, now)
            )
            if state:
                self._set(*state)

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?,?)", (key, json.dumps(value), time.time()))

    def set_state(self, key, value):
        with self.lock, self.db:
            self._set(key, value)

    def get_state(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def pending(self, limit=50, routes=None):
        where = "dead=0 AND due<=?"
        params = [time.time()]
        if routes is not None:
            if not routes:
                return []
            where += " AND route IN (" + ",".join("?" for _ in routes) + ")"
            params.extend(routes)
        with self.lock:
            return self.db.execute(
                f"SELECT * FROM queue WHERE {where} ORDER BY due,id LIMIT ?", (*params, limit)
            ).fetchall()

    def ack(self, row_id):
        with self.lock, self.db:
            self.db.execute("DELETE FROM queue WHERE id=?", (row_id,))

    def retry(self, row_id, delay, dead=False):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE queue SET attempts=attempts+1,due=?,dead=? WHERE id=?",
                (time.time() + delay, int(dead), row_id),
            )

    def close(self):
        with self.lock:
            self.db.close()
