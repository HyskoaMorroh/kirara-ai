"""Regression checks for release workflows that publish project deliverables."""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_windows_release_upload_has_contents_write_permission():
    """The release asset step needs a token permitted to write repository contents."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )

    assert "permissions:\n      contents: write" in workflow
    assert "svenstaro/upload-release-action@v2" in workflow


def test_webui_uses_a_typescript_version_compatible_with_its_vue_types():
    """Vue 3.5 declarations require TypeScript 5.1+ for accessor type support."""
    package = json.loads((PROJECT_ROOT / "webui" / "package.json").read_text(encoding="utf-8"))

    assert package["devDependencies"]["typescript"] == "~5.2.2"


def test_windows_quickstart_builds_the_same_bundled_webui_as_the_docker_image():
    """Quickstart bundles must not silently diverge from the image's WebUI."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-node@v4" in workflow
    assert "yarn install --frozen-lockfile" in workflow
    assert "yarn build" in workflow
    assert 'Copy-Item -Path "webui/dist/*"' in workflow
    assert "DarkSkyTeam/chatgpt-for-bot-webui/releases" not in workflow


def test_prereleases_publish_a_versioned_image_without_replacing_latest():
    """Alpha releases need a testable image while stable users keep their latest tag."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "Published prerelease $RELEASE_TAG will publish only its versioned Docker image." in workflow
    assert 'if [ "${{ steps.release.outputs.publish_latest }}" = "true" ]; then' in workflow


def test_non_latest_releases_still_publish_their_versioned_image():
    """Release authors choose latest independently from availability of versioned images."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "Published release $RELEASE_TAG will publish only its versioned Docker image." in workflow


def test_docker_image_name_accepts_repository_secret_or_variable():
    """Existing repositories may keep the non-sensitive image name in either setting."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "IMAGE_NAME_VARIABLE: ${{ vars.DOCKERHUB_IMAGE }}" in workflow
    assert "IMAGE_NAME_SECRET: ${{ secrets.DOCKERHUB_IMAGE }}" in workflow
    assert 'IMAGE_NAME="${IMAGE_NAME_VARIABLE:-$IMAGE_NAME_SECRET}"' in workflow
