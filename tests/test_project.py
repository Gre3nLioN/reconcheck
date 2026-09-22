from pathlib import Path

import pytest
from PIL import Image

from reconcheck.analysis import analyze_project
from reconcheck.project import ProjectError, discover_project, image_files, project_fingerprint
from reconcheck.server import create_app


def make_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), (80, 120, 160)).save(path)


def test_discovers_image_only_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_image(tmp_path / "images" / "frame-01.jpg")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "cache-root"))

    project = discover_project(tmp_path)

    assert project.images == (tmp_path / "images").resolve()
    assert project.sparse_model is None
    cache = analyze_project(project)
    assert (cache / "quality-report.json").is_file()
    assert create_app(cache).title == "ReconCheck"


def test_explicit_paths_are_relative_to_project(tmp_path: Path) -> None:
    make_image(tmp_path / "source" / "frame.png")

    project = discover_project(tmp_path, images=Path("source"))

    assert project.images == (tmp_path / "source").resolve()
    assert image_files(project.images) == [(tmp_path / "source" / "frame.png").resolve()]


def test_fingerprint_changes_with_image_content(tmp_path: Path) -> None:
    image = tmp_path / "images" / "frame.png"
    make_image(image)
    project = discover_project(tmp_path)
    initial = project_fingerprint(project)
    Image.new("RGB", (32, 24), (160, 80, 20)).save(image)
    assert project_fingerprint(project) != initial


def test_rejects_directory_without_images(tmp_path: Path) -> None:
    (tmp_path / "images").mkdir()

    with pytest.raises(ProjectError, match="No JPEG or PNG images"):
        discover_project(tmp_path)
