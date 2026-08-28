"""The creator identity must have exactly one location on disk.

`FileBasedAuthService` derives `creator.subject` from the resolved password file
directory. `resolve_password_file_path` rewrites the legacy
`data/web/password.hash` to `<DATA_PATH>/web/password.hash`, so the live identity
is `<DATA_PATH>/web/creator.subject`.

This repository shipped *two* subject files with different contents —
`data/creator.subject` and `data/web/creator.subject`. Only the second one is
read, so the first is a stale orphan: any token minted while it was authoritative
silently stops validating, and an operator restoring the wrong file locks
themselves out of every creator-gated operation with no error explaining why.
"""

from __future__ import annotations

import pytest

from kirara_ai.web import app as web_app
from kirara_ai.web.auth.services import FileBasedAuthService


def test_creator_subject_lives_next_to_the_resolved_password_file(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATA_PATH", str(tmp_path))

    resolved = web_app.resolve_password_file_path("data/web/password.hash")
    service = FileBasedAuthService(resolved, "secret")

    assert service.subject_file == resolved.parent / "creator.subject"
    assert service.subject_file.parent == tmp_path / "web"


def test_creator_subject_is_stable_across_restarts(tmp_path):
    password_file = tmp_path / "web" / "password.hash"
    first = FileBasedAuthService(password_file, "secret").creator_subject

    second = FileBasedAuthService(password_file, "secret").creator_subject

    assert first == second


def test_the_subject_file_is_always_the_resolved_web_directory(tmp_path, monkeypatch):
    """A file one directory up must never be mistaken for the live location."""
    monkeypatch.setattr(web_app, "DATA_PATH", str(tmp_path))
    (tmp_path / "creator.subject").write_text("legacy-identity\n", encoding="ascii")

    resolved = web_app.resolve_password_file_path("data/web/password.hash")
    service = FileBasedAuthService(resolved, "secret")

    assert service.subject_file == tmp_path / "web" / "creator.subject"
    assert service.legacy_subject_file == tmp_path / "creator.subject"


def test_a_divergent_legacy_file_never_overrides_the_live_identity(tmp_path, monkeypatch):
    """The live file wins; the legacy one is only adopted when none exists yet.

    The repository currently carries both `data/creator.subject` and
    `data/web/creator.subject` with different contents. Deleting a user's identity
    file is not this code's call, so the contract is: the resolved location is
    authoritative and the other file can never take effect silently.
    """
    monkeypatch.setattr(web_app, "DATA_PATH", str(tmp_path))
    resolved = web_app.resolve_password_file_path("data/web/password.hash")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    (resolved.parent / "creator.subject").write_text("live-identity\n", encoding="ascii")
    (tmp_path / "creator.subject").write_text("legacy-identity\n", encoding="ascii")

    service = FileBasedAuthService(resolved, "secret")

    assert service.creator_subject == "live-identity"


def test_a_legacy_identity_is_adopted_when_no_live_file_exists(tmp_path, monkeypatch):
    """Upgrading past the password-path rewrite must not invalidate issued tokens."""
    monkeypatch.setattr(web_app, "DATA_PATH", str(tmp_path))
    resolved = web_app.resolve_password_file_path("data/web/password.hash")
    (tmp_path / "creator.subject").write_text("pre-rewrite-identity\n", encoding="ascii")

    service = FileBasedAuthService(resolved, "secret")

    assert service.creator_subject == "pre-rewrite-identity"
    # The adopted value is persisted at the resolved location so later reads are stable.
    assert service.subject_file.read_text(encoding="ascii").strip() == "pre-rewrite-identity"


def test_an_invalid_subject_file_fails_loudly(tmp_path):
    password_file = tmp_path / "web" / "password.hash"
    password_file.parent.mkdir(parents=True, exist_ok=True)
    (password_file.parent / "creator.subject").write_text("   \n", encoding="ascii")

    service = FileBasedAuthService(password_file, "secret")

    with pytest.raises(ValueError):
        service.creator_subject


def test_a_token_from_a_different_subject_is_rejected(tmp_path):
    service = FileBasedAuthService(tmp_path / "web" / "password.hash", "secret")
    token = service.create_access_token()
    assert service.get_runtime_principal(token) is not None

    # Simulate restoring the wrong subject file: the old token must stop working
    # rather than silently keep creator rights.
    service.subject_file.write_text("a-different-subject-value\n", encoding="ascii")

    assert service.get_runtime_principal(token) is None
