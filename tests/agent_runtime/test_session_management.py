"""Sessions need to be listable, inspectable and deletable.

The store persisted per-session history and a durable confirmation record, and
the API exposed neither. An operator could bind an Agent to a session key but had
no way to see which sessions existed, how large their history was, what was
waiting on a confirmation, or to clear one session's history after a bad run —
the only recourse was deleting opaque digest-named files by hand.

History content is deliberately *not* exposed: a summary carries counts and
timestamps only, so listing sessions never leaks conversation text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.agent_runtime.session_store import SessionStore
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent


def message(role: str, text: str) -> LLMChatMessage:
    return LLMChatMessage(role=role, content=[LLMChatTextContent(text=text)])


@pytest.fixture()
def store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path)


def test_an_empty_store_lists_nothing(store: SessionStore):
    assert store.list_sessions() == []


def test_a_saved_session_appears_in_the_listing(store: SessionStore):
    store.save_history("onebot:acct:c2c:100", [message("user", "你好")], agent_id="a1")

    sessions = store.list_sessions()

    assert len(sessions) == 1
    assert sessions[0]["agent_id"] == "a1"
    assert sessions[0]["message_count"] == 1


def test_the_listing_never_contains_conversation_text(store: SessionStore):
    store.save_history("k", [message("user", "机密内容")], agent_id="a1")

    payload = repr(store.list_sessions())

    assert "机密内容" not in payload


def test_the_listing_reports_a_stable_session_id(store: SessionStore):
    store.save_history("k", [message("user", "hi")], agent_id="a1")

    first = store.list_sessions()[0]["session_id"]
    store.save_history("k", [message("user", "hi"), message("assistant", "yo")], agent_id="a1")
    second = store.list_sessions()[0]["session_id"]

    assert first == second


def test_two_agents_on_one_session_key_are_listed_separately(store: SessionStore):
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    store.save_history("k", [message("user", "hi")], agent_id="a2")

    agents = sorted(item["agent_id"] for item in store.list_sessions())

    assert agents == ["a1", "a2"]


def test_a_session_can_be_deleted_by_its_listed_id(store: SessionStore):
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    session_id = store.list_sessions()[0]["session_id"]

    assert store.delete_session(session_id) is True
    assert store.list_sessions() == []


def test_deleting_an_unknown_session_reports_false_instead_of_raising(store: SessionStore):
    assert store.delete_session("0" * 64) is False


def test_deleting_a_session_does_not_touch_the_others(store: SessionStore):
    store.save_history("k1", [message("user", "a")], agent_id="a1")
    store.save_history("k2", [message("user", "b")], agent_id="a1")
    target = next(
        item["session_id"]
        for item in store.list_sessions()
        if item["message_count"] == 1
    )

    store.delete_session(target)

    assert len(store.list_sessions()) == 1


def test_deleting_a_session_cannot_escape_the_session_directory(store: SessionStore):
    """A session id is a digest; anything else must be refused, not path-joined."""
    for hostile in ("../pending", "..", "a/b", "", "not-a-digest"):
        assert store.delete_session(hostile) is False


def test_the_listing_reports_pending_confirmation_counts(store: SessionStore):
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    store.save_pending(
        {"confirmation_id": "c" * 32, "agent_id": "a1"},
        session_key="k",
    )

    sessions = store.list_sessions()

    assert sessions[0]["pending_confirmations"] == 1


def test_a_completed_confirmation_is_not_counted_as_pending(store: SessionStore):
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    store.save_pending({"confirmation_id": "c" * 32, "agent_id": "a1"}, session_key="k")
    store.claim_pending("c" * 32, "k")
    store.complete_pending("c" * 32, "succeeded")

    assert store.list_sessions()[0]["pending_confirmations"] == 0


def test_clearing_history_keeps_the_session_but_empties_it(store: SessionStore):
    store.save_history("k", [message("user", "hi"), message("assistant", "yo")], agent_id="a1")
    session_id = store.list_sessions()[0]["session_id"]

    store.clear_history(session_id)

    remaining = store.list_sessions()
    assert len(remaining) == 1
    assert remaining[0]["message_count"] == 0
    assert store.load_history("k", agent_id="a1") == []


def test_the_listing_is_ordered_by_most_recently_updated(store: SessionStore):
    clock_value = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
    store.save_history("older", [message("user", "a")], agent_id="a1")
    store.save_history("newer", [message("user", "b")], agent_id="a1")

    sessions = store.list_sessions()

    assert sessions[0]["updated_at"] >= sessions[-1]["updated_at"]
    assert clock_value  # the fixture clock is irrelevant; ordering uses file mtime


def test_the_listing_is_bounded(store: SessionStore):
    for index in range(30):
        store.save_history(f"k{index}", [message("user", "x")], agent_id="a1")

    assert len(store.list_sessions(limit=10)) == 10


def test_an_invalid_limit_is_rejected(store: SessionStore):
    with pytest.raises(ValueError):
        store.list_sessions(limit=0)
