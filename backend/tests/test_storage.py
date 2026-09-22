"""Storage-level defensive checks: loud failures instead of silent no-ops."""

import pytest

from app.storage import Store


def test_saving_an_unknown_session_fails_loudly(tmp_path):
    store = Store(str(tmp_path / "store.db"))
    with pytest.raises(ValueError, match="not found"):
        store.save("missing-session", {"email_summary": None})


def test_receipt_round_trip_and_conflicting_reuse(tmp_path):
    store = Store(str(tmp_path / "store.db"))
    store.create("s1", "token", {"email_summary": None})
    store.save("s1", {"email_summary": None}, "t1", "hello", {"reply": "ok"})
    assert store.receipt("s1", "t1", "hello") == {"reply": "ok"}
    # Same turn_id with a different message is a conflict, not a silent replay.
    with pytest.raises(ValueError):
        store.receipt("s1", "t1", "different")
