import time

from langgraph_harness import WakeupStore


def test_schedule_and_pending(tmp_path):
    store = WakeupStore(str(tmp_path / "w.db"))
    wid = store.schedule("t1", time.time() + 100, {"type": "wait", "reason": "x"})
    pending = store.pending()
    assert len(pending) == 1
    assert pending[0].id == wid
    assert pending[0].thread_id == "t1"
    assert pending[0].payload["reason"] == "x"


def test_due_filters_by_time(tmp_path):
    store = WakeupStore(str(tmp_path / "w.db"))
    store.schedule("past", time.time() - 10, {"reason": "ya"})
    store.schedule("future", time.time() + 1000, {"reason": "luego"})
    due = store.due()
    assert {w.thread_id for w in due} == {"past"}


def test_due_ordered_by_resume_at(tmp_path):
    store = WakeupStore(str(tmp_path / "w.db"))
    now = time.time()
    store.schedule("b", now - 5, {})
    store.schedule("a", now - 50, {})
    due = store.due()
    assert [w.thread_id for w in due] == ["a", "b"]


def test_delete(tmp_path):
    store = WakeupStore(str(tmp_path / "w.db"))
    wid = store.schedule("t1", time.time() - 1, {})
    store.delete(wid)
    assert store.pending() == []


def test_is_due_property(tmp_path):
    store = WakeupStore(str(tmp_path / "w.db"))
    store.schedule("t1", time.time() - 1, {})
    store.schedule("t2", time.time() + 1000, {})
    pending = {w.thread_id: w for w in store.pending()}
    assert pending["t1"].is_due is True
    assert pending["t2"].is_due is False


def test_persists_across_reopen(tmp_path):
    db = str(tmp_path / "w.db")
    store = WakeupStore(db)
    store.schedule("t1", time.time() + 100, {"reason": "sobrevive"})
    store.close()

    reopened = WakeupStore(db)
    pending = reopened.pending()
    assert len(pending) == 1
    assert pending[0].payload["reason"] == "sobrevive"
