from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from platformdirs import user_data_path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class ProjectError(ValueError):
    """Raised when a project cannot be discovered or validated."""


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    images: Path
    sparse_model: Path | None
    database: Path | None
    dense_cloud: Path | None
    mesh: Path | None

    @property
    def has_reconstruction(self) -> bool:
        return self.sparse_model is not None


def image_files(directory: Path) -> list[Path]:
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not files:
        raise ProjectError(f"No JPEG or PNG images found in {directory}")
    return files


def is_colmap_model(directory: Path) -> bool:
    binary = all(
        (directory / name).is_file() for name in ("cameras.bin", "images.bin", "points3D.bin")
    )
    text = all(
        (directory / name).is_file() for name in ("cameras.txt", "images.txt", "points3D.txt")
    )
    return binary or text


def _resolve(root: Path, value: Path | None) -> Path | None:
    if value is None:
        return None
    candidate = value.expanduser()
    return (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def _first(candidates: Iterable[Path], predicate: object | None = None) -> Path | None:
    for candidate in candidates:
        if predicate is None and candidate.exists():
            return candidate.resolve()
        if callable(predicate) and predicate(candidate):
            return candidate.resolve()
    return None


def discover_project(
    path: Path,
    *,
    images: Path | None = None,
    sparse: Path | None = None,
    database: Path | None = None,
    dense: Path | None = None,
    mesh: Path | None = None,
) -> ProjectPaths:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise ProjectError(f"Project path is not a directory: {root}")

    image_dir = _resolve(root, images) or _first(
        (root / "images", root / "input", root / "dense/images", root), Path.is_dir
    )
    if image_dir is None:
        raise ProjectError("Could not find an image directory; use --images PATH")
    image_files(image_dir)

    sparse_model = _resolve(root, sparse) or _first(
        (root / "sparse/0", root / "sparse", root / "dense/sparse", root), is_colmap_model
    )
    if sparse_model is not None and not is_colmap_model(sparse_model):
        raise ProjectError(f"Not a COLMAP binary or text model: {sparse_model}")

    database_path = _resolve(root, database) or _first((root / "database.db",), Path.is_file)
    dense_cloud = _resolve(root, dense) or _first(
        (root / "dense/fused.ply", root / "fused.ply"), Path.is_file
    )
    mesh_path = _resolve(root, mesh) or _first(
        (
            root / "dense/meshed-poisson.ply",
            root / "dense/meshed-delaunay.ply",
            root / "dense/mesh.ply",
            root / "mesh.ply",
        ),
        Path.is_file,
    )
    for label, candidate in (
        ("database", database_path),
        ("dense cloud", dense_cloud),
        ("mesh", mesh_path),
    ):
        if candidate is not None and not candidate.is_file():
            raise ProjectError(f"{label.capitalize()} does not exist: {candidate}")

    return ProjectPaths(root, image_dir, sparse_model, database_path, dense_cloud, mesh_path)


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


def _update_file_hash(digest: _Digest, path: Path, label: str) -> None:
    digest.update(label.encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)


def project_fingerprint(project: ProjectPaths) -> str:
    digest = hashlib.sha256()
    digest.update(str(project.root).encode())
    for image in image_files(project.images):
        _update_file_hash(digest, image, image.relative_to(project.images).as_posix())
    for candidate in (project.sparse_model, project.database, project.dense_cloud, project.mesh):
        if candidate is None:
            digest.update(b"missing\n")
        elif candidate.is_dir():
            for child in sorted(candidate.iterdir()):
                if child.is_file():
                    _update_file_hash(digest, child, child.name)
        else:
            _update_file_hash(digest, candidate, candidate.name)
    return digest.hexdigest()


def project_cache(project: ProjectPaths) -> Path:
    return user_data_path("reconcheck", "ReconCheck") / "datasets" / project_fingerprint(project)
