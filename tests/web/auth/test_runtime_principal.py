from pathlib import Path

import jwt
import pytest

from kirara_ai.web.auth.principal import RuntimePrincipal
from kirara_ai.web.auth.services import FileBasedAuthService, MockAuthService
from kirara_ai.web.auth.utils import decode_jwt_token


def test_file_auth_persists_one_stable_creator_subject_and_jwt_contains_only_subject(tmp_path: Path):
    password_file = tmp_path / "web" / "password.hash"
    service = FileBasedAuthService(password_file, "secret")

    service.save_password("password")
    first_subject = service.creator_subject
    first_token = service.create_access_token()

    restarted = FileBasedAuthService(password_file, "secret")
    second_token = restarted.create_access_token()

    assert first_subject
    assert restarted.creator_subject == first_subject
    assert decode_jwt_token(first_token, "secret") == {
        "sub": first_subject,
        "exp": decode_jwt_token(first_token, "secret")["exp"],
    }
    assert decode_jwt_token(second_token, "secret")["sub"] == first_subject


def test_auth_service_resolves_server_side_typed_principal_and_does_not_trust_admin_claims(tmp_path: Path):
    service = FileBasedAuthService(tmp_path / "password.hash", "secret")
    service.save_password("password")
    token = service.create_access_token()

    principal = service.get_runtime_principal(token)

    assert isinstance(principal, RuntimePrincipal)
    assert principal.subject == service.creator_subject
    assert principal.is_creator is True
    assert principal.role == "admin"
    assert principal.scopes == frozenset({"*"})

    forged = jwt.encode(
        {"sub": service.creator_subject, "role": "admin", "scopes": ["*"]},
        "secret",
        algorithm="HS256",
    )
    assert service.get_runtime_principal(forged) == principal


def test_admin_role_is_not_creator_without_explicit_creator_state():
    service = MockAuthService(role="admin", scopes=["*"], creator=False, subject="operator")

    principal = service.get_runtime_principal("mock_token")

    assert principal.role == "admin"
    assert principal.is_creator is False


def test_runtime_principal_is_immutable_and_audits_only_subject_digest():
    principal = RuntimePrincipal(
        subject="creator-subject",
        role="admin",
        scopes=frozenset({"*"}),
        is_creator=True,
    )

    with pytest.raises((AttributeError, TypeError)):
        principal.subject = "other"  # type: ignore[misc]

    assert principal.subject_digest
    assert "creator-subject" not in principal.audit_fields()
    assert principal.audit_fields()["subject_digest"] == principal.subject_digest
