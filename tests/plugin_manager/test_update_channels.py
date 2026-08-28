"""Update checks must cover every install source, not only GitHub.

`check_updates` iterated installed Skills and `continue`d on anything whose
`source_metadata.provider` was not `"github"`. Catalog and skills.sh installs
therefore never appeared in the result at all, and the UI — which renders the
returned rows — showed them as having no update available. "No row" and "no
update" are different facts, and only one of them was true.
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.plugin_manager.resource_sources import ResourceSourceService


class StubLifecycle:
    def __init__(self, resources: list[dict[str, Any]]):
        self._resources = resources
        self.imports_path = None

    def list_resources(self, resource_type: str) -> list[dict[str, Any]]:
        return [item for item in self._resources if item.get("type") == resource_type]

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        for item in self._resources:
            if item["resource_id"] == resource_id:
                return item
        raise LookupError(resource_id)


def catalog_skill() -> dict[str, Any]:
    return {
        "resource_id": "skill.from-catalog",
        "type": "skill",
        "current_version": "1.0.0",
        "content_sha256": "abc",
        "source_key": "catalog:agent-browser",
        "source_metadata": {"provider": "catalog", "catalog_id": "agent-browser"},
    }


def skills_sh_skill() -> dict[str, Any]:
    return {
        "resource_id": "skill.from-skills-sh",
        "type": "skill",
        "current_version": "2.0.0",
        "content_sha256": "def",
        "source_key": "skills.sh:writing",
        "source_metadata": {"provider": "skills.sh", "slug": "writing"},
    }


def service(resources: list[dict[str, Any]]) -> ResourceSourceService:
    instance = object.__new__(ResourceSourceService)
    instance.lifecycle = StubLifecycle(resources)  # type: ignore[attr-defined]
    return instance


def test_a_catalog_skill_appears_in_the_update_report():
    results = service([catalog_skill()]).check_updates()

    assert len(results) == 1
    assert results[0]["resource_id"] == "skill.from-catalog"


def test_an_unsupported_source_is_labeled_instead_of_omitted():
    results = service([catalog_skill()]).check_updates()

    row = results[0]
    assert row["update_channel_supported"] is False
    assert row["update_available"] is False
    assert row["source_provider"] == "catalog"
    # The user must be told what to do, not just that nothing happened.
    assert "重新安装" in row["error"]


def test_every_non_github_source_is_reported():
    results = service([catalog_skill(), skills_sh_skill()]).check_updates()

    providers = {row["source_provider"] for row in results}
    assert providers == {"catalog", "skills.sh"}


def test_the_current_version_is_still_reported_for_an_unsupported_source():
    results = service([skills_sh_skill()]).check_updates()

    assert results[0]["current_version"] == "2.0.0"
    assert results[0]["current_content_sha256"] == "def"


def test_a_resource_without_source_metadata_is_reported_as_unsupported():
    """A locally imported Skill has no remote source; say so rather than hiding it."""
    orphan = {
        "resource_id": "skill.orphan",
        "type": "skill",
        "current_version": "1.0.0",
        "content_sha256": "ghi",
        "source_metadata": None,
    }

    results = service([orphan]).check_updates()

    assert len(results) == 1
    assert results[0]["update_channel_supported"] is False
    assert results[0]["source_provider"] is None


@pytest.mark.parametrize("provider", ["catalog", "skills.sh", "local"])
def test_unsupported_providers_never_claim_an_update_is_available(provider: str):
    resource = catalog_skill()
    resource["source_metadata"] = {"provider": provider}

    results = service([resource]).check_updates()

    assert results[0]["update_available"] is False
    assert results[0]["remote_content_sha256"] is None
