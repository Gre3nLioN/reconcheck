from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import laspy
import numpy as np

from reconcheck.analysis import PROFILE, sha256_file
from reconcheck.project import ProjectError


def _write_ply(path: Path, vertices: np.ndarray, faces: np.ndarray | None = None) -> None:
    faces = faces if faces is not None else np.empty((0, 3), dtype=np.int32)
    with path.open("wb") as output:
        output.write(b"ply\nformat binary_little_endian 1.0\n")
        output.write(f"element vertex {len(vertices)}\n".encode())
        output.write(b"property float x\nproperty float y\nproperty float z\n")
        output.write(f"element face {len(faces)}\n".encode())
        output.write(b"property list uchar int vertex_indices\nend_header\n")
        vertices.astype("<f4", copy=False).tofile(output)
        for face in faces.astype("<i4", copy=False):
            output.write(struct.pack("<Biii", 3, *map(int, face)))


def _convert_obj(source: Path, target: Path) -> None:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "v":
            vertices.append(tuple(float(value) for value in fields[1:4]))
        elif len(fields) >= 4 and fields[0] == "f":
            indexes = [int(value.split("/")[0]) - 1 for value in fields[1:]]
            for index in range(1, len(indexes) - 1):
                faces.append((indexes[0], indexes[index], indexes[index + 1]))
    if not vertices or not faces:
        raise ProjectError(f"WebODM OBJ has no usable geometry: {source}")
    _write_ply(target, np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.int32))


def _convert_laz(source: Path, target: Path, maximum_points: int) -> None:
    cloud = laspy.read(source)
    points = np.column_stack((cloud.x, cloud.y, cloud.z)).astype(np.float32)
    if len(points) > maximum_points:
        indexes = np.linspace(0, len(points) - 1, maximum_points, dtype=np.int64)
        points = points[indexes]
    _write_ply(target, points)


def _camera_markers(images_path: Path) -> list[dict[str, Any]]:
    records = json.loads(images_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ProjectError(f"WebODM images.json must contain a list: {images_path}")
    valid = [item for item in records if item.get("latitude") is not None]
    if not valid:
        return []
    lat0 = math.radians(float(valid[0]["latitude"]))
    lon0 = float(valid[0]["longitude"])
    lat0_degrees = float(valid[0]["latitude"])
    meters_per_degree = 111_320.0
    cameras = []
    for index, item in enumerate(valid):
        x = (float(item["longitude"]) - lon0) * meters_per_degree * math.cos(lat0)
        z = (float(item["latitude"]) - lat0_degrees) * meters_per_degree
        y = float(item.get("altitude") or 0.0) - float(valid[0].get("altitude") or 0.0)
        cameras.append(
            {
                "id": index,
                "image_id": str(index),
                "name": str(item.get("filename", f"camera-{index:04d}")),
                "camera_model": "WebODM GPS metadata; orientation unavailable",
                "width": int(item.get("width", 0)),
                "height": int(item.get("height", 0)),
                "intrinsics": [],
                "cam_from_world": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
                "center": [x, y, z],
                "image_url": "",
            }
        )
    return cameras


def prepare_webodm(root: Path, output: Path, *, maximum_points: int = 2_000_000) -> Path:
    root, output = root.expanduser().resolve(), output.expanduser().resolve()
    if not root.is_dir():
        raise ProjectError(f"WebODM export is not a directory: {root}")
    obj = root / "odm_texturing/odm_textured_model_geo.obj"
    laz = root / "odm_georeferencing/odm_georeferenced_model.laz"
    images = root / "images.json"
    for path in (obj, laz, images):
        if not path.is_file():
            raise ProjectError(f"WebODM export is missing: {path}")
    output.mkdir(parents=True, exist_ok=True)
    mesh = output / "mesh.ply"
    dense = output / "dense.ply"
    if not mesh.is_file():
        _convert_obj(obj, mesh)
    if not dense.is_file():
        _convert_laz(laz, dense, maximum_points)
    cameras = _camera_markers(images)
    diagnostics = {
        "schema_version": 1,
        "registration": {
            "input_images": len(cameras),
            "registered_images": 0,
            "registration_ratio": 0.0,
        },
        "reprojection_error_px": {
            "mean": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "histogram_bins_px": [],
            "histogram_counts": [],
        },
        "tracks": {
            "points": 0,
            "mean_length": 0.0,
            "median_length": 0.0,
            "p10_length": 0.0,
            "length_counts": {},
        },
        "view_angle_degrees": {
            "points_with_two_or_more_views": 0,
            "median": 0.0,
            "p10": 0.0,
            "p95": 0.0,
            "histogram_bins_degrees": [],
            "histogram_counts": [],
        },
        "camera_centers": [camera["center"] for camera in cameras],
        "per_image": [],
    }
    scene = {
        "schema_version": 1,
        "id": output.name,
        "name": "Torre di Pisa — WebODM mesh-only export",
        "coordinate_convention": (
            "Local ENU-like coordinates from WebODM GPS metadata; camera orientation unavailable"
        ),
        "stats": {
            "registered_images": 0,
            "input_images": len(cameras),
            "sparse_points": 0,
            "mean_reprojection_error_px": 0.0,
        },
        "assets": {
            "sparse_cloud": None,
            "dense_cloud": f"{dense}",
            "mesh": f"{mesh}",
            "sparse_quality": None,
        },
        "cameras": cameras,
        "capabilities": {
            "images": "unavailable",
            "camera_poses": f"metadata: {len(cameras)}; orientation unavailable",
        },
    }
    report = {
        "schema_version": 1,
        "report_type": "photogrammetry-quality-report",
        "generator": {"name": "reconcheck-webodm-adapter", "version": "0.1.0"},
        "dataset": {
            "dataset_id": output.name,
            "name": scene["name"],
            "local_dataset_path": str(root),
            "image_count": 0,
            "source_fingerprint_sha256": sha256_file(obj),
        },
        "quality_profile": PROFILE,
        "verdict": {
            "status": "unverified",
            "checks": [],
            "findings": [
                "WebODM mesh-only export loaded.",
                "Source images and camera orientations are unavailable.",
            ],
            "recommendations": [],
            "scope": (
                "Existing WebODM outputs; no fresh image or COLMAP quality analysis was performed."
            ),
        },
        "input_image_health": {"image_count": 0, "per_image": []},
        "provenance": {
            "assets": {
                "mesh": {"bytes": mesh.stat().st_size, "sha256": sha256_file(mesh)},
                "dense_cloud": {"bytes": dense.stat().st_size, "sha256": sha256_file(dense)},
            }
        },
    }
    metadata = {
        "project": {
            "root": str(root),
            "images": None,
            "sparse_model": None,
            "database": None,
            "dense_cloud": str(dense),
            "mesh": str(mesh),
        },
        "images": [],
    }
    for name, payload in (
        ("manifest.json", scene),
        ("diagnostics.json", diagnostics),
        ("quality-report.json", report),
        ("metadata.json", metadata),
    ):
        (output / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output
