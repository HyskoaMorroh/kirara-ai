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


def test_windows_quickstart_publishes_only_for_the_latest_formal_release():
    """Pre-releases and non-Latest releases must not spend runner time on Windows publishing."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )
    triggers = workflow.split("concurrency:", maxsplit=1)[0]

    assert "workflow_dispatch:" in triggers
    assert "release:\n    types:\n      - published" in triggers
    assert "push:" not in triggers
    assert "Verify latest release eligibility" in workflow
    assert 'RELEASE_IS_PRERELEASE: ${{ github.event.release.prerelease }}' in workflow
    assert 'gh api "repos/${GITHUB_REPOSITORY}/releases/latest" --jq \'.tag_name\'' in workflow
    assert "needs.release.outputs.publish == 'true'" in workflow
    assert "ref: ${{ github.event.release.tag_name || github.sha }}" in workflow


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


def test_docker_workflows_share_a_cross_release_registry_cache():
    """Version tags must reuse the same Buildx cache instead of rebuilding from scratch."""
    for filename in ("docker-latest.yml", "docker-tag.yml"):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / filename).read_text(
            encoding="utf-8"
        )

        assert "cache-from: type=registry,ref=${{ steps.image.outputs.name }}:buildcache" in workflow
        assert (
            "cache-to: type=registry,ref=${{ steps.image.outputs.name }}:buildcache,"
            "mode=max,image-manifest=true,oci-mediatypes=true" in workflow
        )


def test_release_builds_inject_their_version_into_the_bundled_webui():
    """Published artifacts must expose the release tag instead of an unknown UI version."""
    docker_release_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml"
    ).read_text(encoding="utf-8")
    docker_tag_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml"
    ).read_text(encoding="utf-8")
    windows_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml"
    ).read_text(encoding="utf-8")

    assert "VITE_APP_VERSION=${{ github.event.release.tag_name" in docker_release_workflow
    assert "VITE_APP_VERSION=${{ steps.vars.outputs.tag }}" in docker_tag_workflow
    assert "VITE_APP_VERSION: ${{ github.event.release.tag_name" in windows_workflow


def test_release_preflight_checks_contracts_and_the_versioned_webui_build():
    """A quick check must catch front-end regressions before a costly release build starts."""
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml"
    ).read_text(encoding="utf-8")

    assert "branches: [main, master]" in workflow
    assert "python -m pytest tests/test_release_workflow_contract.py tests/test_webui_build_contract.py -q" in workflow
    assert "yarn type-check" in workflow
    assert "VITE_APP_VERSION: v0.0.0-ci" in workflow
    assert "yarn build" in workflow
