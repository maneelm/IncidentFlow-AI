import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "check_release", Path(__file__).resolve().parents[3] / "scripts/check_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "frontend/node_modules/file.js",
        ".tools/private.dump",
        "backups/private.dump",
    ],
)
def test_private_and_generated_paths_fail_publication(path):
    assert release.forbidden_path(path)


def test_example_and_source_paths_are_publishable():
    assert release.forbidden_path("backups/private.sql")
    assert not release.forbidden_path(".env.example")
    assert not release.forbidden_path("frontend/src/features/incidents/IncidentList.tsx")


def test_secret_findings_report_categories_never_values():
    private = b"synthetic-runtime-secret-for-this-test"
    found = release.findings("app.py", b"prefix" + private, [private])
    assert found == ["local_runtime_secret"]
    assert private.decode() not in repr(found)


def test_relative_markdown_links_are_verified(tmp_path):
    (tmp_path / "present.md").touch()
    found = release.missing_links(
        tmp_path / "README.md",
        "[good](present.md#part) [bad](missing.md) [external](https://example.com)",
    )
    assert found == ["missing.md"]


def test_staged_check_reads_index_instead_of_changed_working_file(tmp_path, monkeypatch):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / "app.py"
    path.write_text("reviewed snapshot", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True, capture_output=True)
    path.write_text("later unstaged content", encoding="utf-8")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    assert release.publication_files(True) == [(path, b"reviewed snapshot")]


def test_staged_check_refuses_an_ancestor_git_root(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path / "project")
    monkeypatch.setattr(release, "git", lambda *args: str(tmp_path).encode())
    with pytest.raises(ValueError, match="initialize_git_in_project_directory"):
        release.publication_files(True)
