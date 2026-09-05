from abc import ABC, abstractmethod
from datetime import timedelta
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Mapping, Optional

from kirara_ai.logger import get_logger

from .principal import RuntimePrincipal

logger = get_logger("Auth")


class AuthService(ABC):
    @abstractmethod
    def is_first_time(self) -> bool:
        pass

    @abstractmethod
    def save_password(self, password: str) -> None:
        pass

    @abstractmethod
    def verify_password(self, password: str) -> bool:
        pass

    @abstractmethod
    def create_access_token(self, expires_delta: Optional[timedelta] = None) -> str:
        pass

    @abstractmethod
    def verify_token(self, token: str) -> bool:
        pass

    @abstractmethod
    def get_runtime_principal(self, token: str) -> RuntimePrincipal | None:
        pass

    def get_token_claims(self, token: str) -> Mapping[str, Any] | None:
        """Return claims while keeping legacy AuthService implementations valid."""
        principal = self.get_runtime_principal(token)
        if principal is None:
            return None
        return {
            "sub": principal.subject,
            "role": principal.role,
            "scopes": sorted(principal.scopes),
            "is_creator": principal.is_creator,
        }


class FileBasedAuthService(AuthService):
    def __init__(self, password_file: Path, secret_key: str):
        self.password_file = password_file
        self.subject_file = password_file.with_name("creator.subject")
        self.secret_key = secret_key

    @property
    def legacy_subject_file(self) -> Path:
        """The pre-rewrite location of the creator identity.

        ``resolve_password_file_path`` rewrites the legacy
        ``data/web/password.hash`` to ``<DATA_PATH>/web/password.hash``, which
        moved the derived ``creator.subject`` one directory down. An installation
        that minted its identity before that rewrite still has the file at
        ``<DATA_PATH>/creator.subject``; adopting it keeps existing tokens valid
        instead of silently issuing a new identity and logging everyone out.
        """
        return self.subject_file.parent.parent / "creator.subject"

    def _read_subject(self, path: Path) -> str:
        subject = path.read_text(encoding="ascii").strip()
        if subject and len(subject) <= 256:
            return subject
        raise ValueError("creator subject file is invalid")

    @property
    def creator_subject(self) -> str:
        if self.subject_file.exists():
            subject = self._read_subject(self.subject_file)
            legacy = self.legacy_subject_file
            if legacy.exists() and legacy != self.subject_file:
                try:
                    stale = legacy.read_text(encoding="ascii").strip()
                except OSError:
                    stale = ""
                if stale and stale != subject:
                    # Two divergent identities on disk: only this one is read, so
                    # say so once rather than letting a later "restore" of the
                    # other file invalidate every issued token with no
                    # explanation.
                    logger.warning(
                        "Ignoring a stale creator identity at {}; the active identity is {}",
                        legacy,
                        self.subject_file,
                    )
            return subject

        self.subject_file.parent.mkdir(parents=True, exist_ok=True)

        legacy = self.legacy_subject_file
        if legacy.exists() and legacy != self.subject_file:
            # Adopt the pre-rewrite identity so tokens minted before the path
            # change keep working.
            adopted = self._read_subject(legacy)
            self._write_subject(adopted)
            logger.info("Adopted the existing creator identity from {}", legacy)
            return self._read_subject(self.subject_file)

        return self._write_subject(secrets.token_urlsafe(32))

    def _write_subject(self, subject: str) -> str:
        """Publish the creator identity atomically, never truncating an existing one."""
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".creator-subject-",
            suffix=".tmp",
            dir=self.subject_file.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as file:
                file.write(subject)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            try:
                os.replace(temporary_path, self.subject_file)
            except OSError:
                if not self.subject_file.exists():
                    raise
            return self._read_subject(self.subject_file)
        finally:
            temporary_path.unlink(missing_ok=True)

    def is_first_time(self) -> bool:
        return not self.password_file.exists()

    def save_password(self, password: str) -> None:
        from .utils import hash_password

        self.password_file.parent.mkdir(parents=True, exist_ok=True)
        self.creator_subject
        hashed = hash_password(password)
        with open(self.password_file, "wb") as f:
            f.write(hashed)

    def verify_password(self, password: str) -> bool:
        from .utils import verify_password

        if not self.password_file.exists():
            return False

        with open(self.password_file, "rb") as f:
            hashed = f.read()
        return verify_password(password, hashed)

    def create_access_token(self, expires_delta: Optional[timedelta] = None) -> str:
        from .utils import create_jwt_token

        return create_jwt_token(
            self.secret_key,
            expires_delta,
            subject=self.creator_subject,
        )

    def verify_token(self, token: str) -> bool:
        from .utils import verify_jwt_token

        return verify_jwt_token(token, self.secret_key)

    def get_token_claims(self, token: str) -> Mapping[str, Any] | None:
        return super().get_token_claims(token)

    def get_runtime_principal(self, token: str) -> RuntimePrincipal | None:
        from .utils import decode_jwt_token

        claims = decode_jwt_token(token, self.secret_key)
        if claims is None:
            return None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not secrets.compare_digest(
            subject,
            self.creator_subject,
        ):
            return None
        return RuntimePrincipal(
            subject=self.creator_subject,
            role="admin",
            scopes=frozenset({"*"}),
            is_creator=True,
        )


class MockAuthService(AuthService):
    def __init__(
        self,
        *,
        role: str = "admin",
        scopes: Optional[list[str]] = None,
        creator: bool = False,
        subject: str = "mock-subject",
    ):
        self._password = None
        self._is_first_time = True
        self.role = role
        self.scopes = list(["*"] if scopes is None else scopes)
        self.creator = bool(creator)
        self.subject = subject

    def is_first_time(self) -> bool:
        return self._is_first_time

    def save_password(self, password: str) -> None:
        self._password = password
        self._is_first_time = False

    def verify_password(self, password: str) -> bool:
        return password == self._password

    def create_access_token(self, expires_delta: Optional[timedelta] = None) -> str:
        return "mock_token"

    def verify_token(self, token: str) -> bool:
        return token == "mock_token"

    def get_token_claims(self, token: str) -> Mapping[str, Any] | None:
        return super().get_token_claims(token)

    def get_runtime_principal(self, token: str) -> RuntimePrincipal | None:
        if not self.verify_token(token):
            return None
        return RuntimePrincipal(
            subject=self.subject,
            role=self.role,
            scopes=frozenset(self.scopes),
            is_creator=self.creator,
        )
