from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class RuntimePrincipal:
    """Server-resolved request identity used by runtime policy boundaries."""

    subject: str
    role: str = "user"
    scopes: frozenset[str] = frozenset()
    is_creator: bool = False

    def __post_init__(self) -> None:
        subject = str(self.subject).strip()
        if not subject or len(subject) > 256:
            raise ValueError("runtime principal subject is invalid")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "role", str(self.role).strip().lower() or "user")
        object.__setattr__(
            self,
            "scopes",
            frozenset(str(scope).strip() for scope in self.scopes if str(scope).strip()),
        )

    @property
    def subject_digest(self) -> str:
        return hashlib.sha256(self.subject.encode("utf-8")).hexdigest()

    def has_scopes(self, required_scopes: tuple[str, ...]) -> bool:
        return (
            not required_scopes
            or "*" in self.scopes
            or set(required_scopes).issubset(self.scopes)
        )

    def audit_fields(self) -> dict[str, str]:
        return {"subject_digest": self.subject_digest}


_CURRENT_PRINCIPAL: ContextVar[Optional[RuntimePrincipal]] = ContextVar(
    "kirara_runtime_principal",
    default=None,
)


def get_runtime_principal() -> Optional[RuntimePrincipal]:
    return _CURRENT_PRINCIPAL.get()


@contextmanager
def runtime_principal_context(
    principal: Optional[RuntimePrincipal],
) -> Iterator[Optional[RuntimePrincipal]]:
    token = _CURRENT_PRINCIPAL.set(principal)
    try:
        yield principal
    finally:
        _CURRENT_PRINCIPAL.reset(token)
