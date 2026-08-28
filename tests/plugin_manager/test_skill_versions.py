"""Remote Skill versions must come from the source, not be synthesized.

`install_skill` hardcoded `version="1.0.0"` and `update_skill` auto-bumped the
patch number, so a Skill declaring `version: 2.1.0` in its SKILL.md front matter
was installed as 1.0.0 and every update produced 1.0.1, 1.0.2, ... The
downgrade protection in `ResourceLifecycleService` compares semver, so against a
synthetic sequence it can never fire: reinstalling an older upstream Skill is
accepted as an "upgrade".
"""

from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.resource_sources import ResourceSourceService


def front_matter(**fields: str) -> bytes:
    body = "\n".join(f"{key}: {value}" for key, value in fields.items())
    return f"---\n{body}\n---\n\n# Skill body\n".encode("utf-8")


def parse(content: bytes) -> dict[str, str]:
    return ResourceSourceService._parse_skill_front_matter(content)


def test_front_matter_version_is_read():
    parsed = parse(front_matter(name="agent-browser", description="d", version="2.1.0"))

    assert parsed["version"] == "2.1.0"


def test_a_missing_version_falls_back_to_the_documented_default():
    parsed = parse(front_matter(name="agent-browser", description="d"))

    assert parsed["version"] == "1.0.0"


def test_a_non_semver_version_falls_back_instead_of_breaking_install():
    parsed = parse(front_matter(name="s", description="d", version="not-a-version"))

    assert parsed["version"] == "1.0.0"


def test_a_quoted_version_is_unquoted():
    parsed = parse(front_matter(name="s", description="d", version="'3.4.5'"))

    assert parsed["version"] == "3.4.5"


def test_a_prerelease_version_is_preserved():
    parsed = parse(front_matter(name="s", description="d", version="1.2.0rc1"))

    assert parsed["version"] == "1.2.0rc1"


def test_content_without_front_matter_still_yields_a_default_version():
    parsed = parse(b"# Skill without front matter\n")

    assert parsed == {"name": "", "description": "", "version": "1.0.0"}


def test_next_version_prefers_the_remote_version_when_it_is_higher():
    assert ResourceSourceService._next_version("1.0.0", remote_version="2.1.0") == "2.1.0"


def test_next_version_bumps_locally_when_the_remote_version_did_not_move():
    """A repository that edits a Skill without touching its version still needs a new local version."""
    assert ResourceSourceService._next_version("1.0.0", remote_version="1.0.0") == "1.0.1"


def test_next_version_bumps_locally_when_the_remote_version_went_backwards():
    """A downgraded upstream version must not become the installed version."""
    assert ResourceSourceService._next_version("2.0.0", remote_version="1.0.0") == "2.0.1"


def test_next_version_without_a_remote_version_keeps_the_legacy_bump():
    assert ResourceSourceService._next_version("1.0.4") == "1.0.5"


@pytest.mark.parametrize("current", ["", "not-a-version"])
def test_next_version_recovers_from_an_unparsable_current_version(current: str):
    assert ResourceSourceService._next_version(current, remote_version="1.2.3") == "1.2.3"
