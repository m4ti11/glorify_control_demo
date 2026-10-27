import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from glorify_control_agent.storage.spool import Spool


def test_durable_retry_and_ack(settings):
    queue = Spool(settings.spool_path)
    queue.enqueue("events", '{"event_id":"stable"}')
    row = queue.pending()[0]
    queue.retry(row["id"], 0)
    queue.close()
    queue = Spool(settings.spool_path)
    replay = queue.pending()[0]
    assert replay["payload"] == row["payload"]
    assert replay["attempts"] == 1
    queue.ack(replay["id"])
    assert not queue.pending()
    queue.close()


def test_due_and_quarantine(spool):
    spool.enqueue("events", "{}")
    row_id = spool.pending()[0]["id"]
    spool.retry(row_id, 3600)
    assert not spool.pending()
    spool.retry(row_id, 0, dead=True)
    assert not spool.pending()
    assert spool.db.execute("SELECT count(*) FROM queue WHERE dead=1").fetchone()[0] == 1


def test_retention_and_capacity(tmp_path):
    queue = Spool(str(tmp_path / "bounded.db"), max_bytes=1024 * 1024, max_items=3, retention=60)
    for _ in range(10):
        queue.enqueue("events", "x" * 200000)
    assert len(queue.pending()) <= 2
    assert Path(tmp_path / "bounded.db").stat().st_size <= 1024 * 1024
    assert not Path(tmp_path / "bounded.db-wal").exists()
    with queue.db:
        queue.db.execute("UPDATE queue SET created=?", (time.time() - 61,))
    queue.prune()
    assert not queue.pending()
    queue.close()


def test_item_limit_and_state_atomicity(tmp_path):
    queue = Spool(str(tmp_path / "queue.db"), max_items=3)
    for i in range(4):
        queue.enqueue("events", str(i), state=("latest", i))
    assert [r["payload"] for r in queue.pending()] == ["1", "2", "3"]
    assert queue.get_state("latest") == 3
    with pytest.raises(ValueError):
        queue.enqueue("events", "x" * 250000, state=("latest", 4))
    assert queue.get_state("latest") == 3
    queue.close()


def test_concurrent_producers(spool):
    def produce(i):
        for j in range(20):
            spool.enqueue("events", f"{i}:{j}")

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(produce, range(4)))
    assert len(spool.pending(100)) == 80


def test_ids_are_not_reused_after_ack(spool):
    spool.enqueue("events", "first")
    first = spool.pending()[0]["id"]
    spool.ack(first)
    spool.enqueue("events", "second")
    assert spool.pending()[0]["id"] > first
    # A late acknowledgement cannot delete a newer event after a capacity eviction.
    spool.ack(first)
    assert spool.pending()[0]["payload"] == "second"


def test_pruning_does_not_evict_at_exact_item_limit(tmp_path):
    queue = Spool(str(tmp_path / "exact.db"), max_items=3)
    for i in range(3):
        queue.enqueue("events", str(i))
    queue.prune()
    assert len(queue.pending()) == 3
    queue.close()
