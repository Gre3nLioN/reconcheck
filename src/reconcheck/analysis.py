from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pycolmap
from PIL import Image

from reconcheck.project import ProjectPaths, image_files, project_cache

PROFILE: dict[str, Any] = {
    "id": "general-photogrammetry-v1",
    "description": (
        "Internal reconstruction-quality thresholds for general photogrammetry; "
        "not a ground-truth accuracy guarantee."
    ),
    "requirements": {
        "registration_ratio_min": 0.95,
        "reprojection_p95_px_max": 2.0,
        "median_track_length_min": 4.0,
        "median_view_angle_deg_min": 8.0,
        "coverage_sector_ratio_min": 0.75,
        "dense_output_required": True,
        "mesh_output_required": True,
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def histogram(values: list[float], bins: list[float]) -> list[int]:
    counts = [0] * (len(bins) + 1)
    for value in values:
        index = next((index for index, upper in enumerate(bins) if value <= upper), len(bins))
        counts[index] += 1
    return counts


def _dhash(gray: np.ndarray) -> str:
    resized = Image.fromarray(gray).resize((9, 8), Image.Resampling.BILINEAR)
    values = np.asarray(resized)
    bits = values[:, 1:] > values[:, :-1]
    return f"{int(''.join('1' if bit else '0' for bit in bits.flat), 2):016x}"


def _image_health(path: Path, image_id: str, relative_name: str) -> dict[str, Any]:
    with Image.open(path) as source:
        width, height = source.size
        grayscale = source.convert("L")
        grayscale.thumbnail((256, 256))
        gray = np.asarray(grayscale, dtype=np.float32) / 255.0
    laplacian = (
        -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    )
    return {
        "id": image_id,
        "name": relative_name,
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "brightness_mean": round(float(gray.mean()), 6),
        "contrast_stddev": round(float(gray.std()), 6),
        "blur_laplacian_variance": round(float(laplacian.var()), 8),
        "shadow_clipping_ratio": round(float((gray <= 0.01).mean()), 6),
        "highlight_clipping_ratio": round(float((gray >= 0.99).mean()), 6),
        "dhash": _dhash((gray * 255).astype(np.uint8)),
    }


def _empty_diagnostics(image_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "registration": {
            "input_images": len(image_records),
            "registered_images": 0,
            "registration_ratio": 0.0,
        },
        "reprojection_error_px": {
            "mean": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "histogram_bins_px": [0.25, 0.5, 1.0, 2.0, 5.0],
            "histogram_counts": [0, 0, 0, 0, 0],
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
            "histogram_bins_degrees": [5, 10, 20, 45],
            "histogram_counts": [0, 0, 0, 0, 0],
        },
        "camera_centers": [],
        "per_image": [
            {
                "image_id": item["id"],
                "name": item["name"],
                "registered": False,
                "observations": 0,
                "mean_reprojection_error_px": None,
                "max_reprojection_error_px": None,
            }
            for item in image_records
        ],
    }


def _reconstruction_outputs(
    project: ProjectPaths, cache: Path, image_records: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if project.sparse_model is None:
        return (
            _empty_diagnostics(image_records),
            [],
            {"sparse_cloud": None, "sparse_quality": None, "dense_cloud": None, "mesh": None},
        )
    reconstruction = pycolmap.Reconstruction(project.sparse_model)  # type: ignore[attr-defined]
    reconstruction.update_point_3d_errors()
    reconstruction.export_PLY(cache / "sparse.ply")
    points = list(reconstruction.points3D.values())
    image_id_by_name = {str(item["name"]): str(item["id"]) for item in image_records}
    basename_ids = {Path(str(item["name"])).name: str(item["id"]) for item in image_records}
    point_angles: list[float] = []
    with (cache / "sparse-quality.bin").open("wb") as output:
        output.write(struct.pack("<4sHHI", b"PQAT", 1, 2, len(points)))
        for point in points:
            directions = []
            for element in point.track.elements:
                image = reconstruction.images[element.image_id]
                if image.has_pose:
                    direction = image.projection_center() - point.xyz
                    length = math.sqrt(float(direction @ direction))
                    if length:
                        directions.append(direction / length)
            maximum = 0.0
            for left_index, left in enumerate(directions):
                for right in directions[left_index + 1 :]:
                    maximum = max(
                        maximum, math.degrees(math.acos(max(-1.0, min(1.0, float(left @ right)))))
                    )
            if len(directions) >= 2:
                point_angles.append(maximum)
            output.write(
                struct.pack(
                    "<HHff",
                    point.track.length(),
                    0,
                    maximum,
                    point.error if point.has_error() else 0.0,
                )
            )

    image_reports = []
    cameras = []
    registered = [image for image in reconstruction.images.values() if image.has_pose]
    for image in sorted(reconstruction.images.values(), key=lambda item: item.name):
        public_id = image_id_by_name.get(image.name, basename_ids.get(Path(image.name).name))
        if public_id is None:
            continue
        observed = [
            reconstruction.points3D[point.point3D_id]
            for point in image.points2D
            if point.has_point3D() and point.point3D_id in reconstruction.points3D
        ]
        errors = [point.error for point in observed if point.has_error()]
        image_reports.append(
            {
                "image_id": public_id,
                "name": image.name,
                "registered": image.has_pose,
                "observations": len(observed),
                "mean_reprojection_error_px": sum(errors) / len(errors) if errors else None,
                "max_reprojection_error_px": max(errors) if errors else None,
            }
        )
        if image.has_pose:
            camera = reconstruction.cameras[image.camera_id]
            cameras.append(
                {
                    "id": image.image_id,
                    "image_id": public_id,
                    "name": image.name,
                    "camera_model": camera.model.name,
                    "width": camera.width,
                    "height": camera.height,
                    "intrinsics": camera.params.tolist(),
                    "cam_from_world": image.cam_from_world().matrix().reshape(-1).tolist(),
                    "center": image.projection_center().tolist(),
                    "image_url": f"/api/images/{public_id}",
                }
            )
    report_by_id = {str(item["image_id"]): item for item in image_reports}
    for item in image_records:
        report_by_id.setdefault(
            str(item["id"]),
            {
                "image_id": item["id"],
                "name": item["name"],
                "registered": False,
                "observations": 0,
                "mean_reprojection_error_px": None,
                "max_reprojection_error_px": None,
            },
        )
    point_errors = [point.error for point in points if point.has_error()]
    tracks = [point.track.length() for point in points]
    diagnostics = {
        "schema_version": 1,
        "registration": {
            "input_images": len(image_records),
            "registered_images": len(registered),
            "registration_ratio": len(registered) / len(image_records) if image_records else 0.0,
        },
        "reprojection_error_px": {
            "mean": sum(point_errors) / len(point_errors) if point_errors else 0.0,
            "median": median(point_errors) if point_errors else 0.0,
            "p95": percentile(point_errors, 0.95),
            "max": max(point_errors) if point_errors else 0.0,
            "histogram_bins_px": [0.25, 0.5, 1.0, 2.0, 5.0],
            "histogram_counts": histogram(point_errors, [0.25, 0.5, 1.0, 2.0]),
        },
        "tracks": {
            "points": len(points),
            "mean_length": sum(tracks) / len(tracks) if tracks else 0.0,
            "median_length": median(tracks) if tracks else 0.0,
            "p10_length": percentile([float(value) for value in tracks], 0.1),
            "length_counts": {str(value): tracks.count(value) for value in sorted(set(tracks))},
        },
        "view_angle_degrees": {
            "points_with_two_or_more_views": len(point_angles),
            "median": median(point_angles) if point_angles else 0.0,
            "p10": percentile(point_angles, 0.1),
            "p95": percentile(point_angles, 0.95),
            "histogram_bins_degrees": [5, 10, 20, 45],
            "histogram_counts": [
                sum(value < 5 for value in point_angles),
                sum(5 <= value < 10 for value in point_angles),
                sum(10 <= value < 20 for value in point_angles),
                sum(20 <= value < 45 for value in point_angles),
                sum(value >= 45 for value in point_angles),
            ],
        },
        "camera_centers": [image.projection_center().tolist() for image in registered],
        "per_image": [report_by_id[str(item["id"])] for item in image_records],
    }
    assets = {
        "sparse_cloud": "/api/assets/sparse.ply",
        "sparse_quality": "/api/assets/sparse-quality.bin",
        "dense_cloud": "/api/assets/dense-cloud.ply" if project.dense_cloud else None,
        "mesh": "/api/assets/mesh.ply" if project.mesh else None,
    }
    return diagnostics, cameras, assets


def analyze_project(project: ProjectPaths, *, refresh: bool = False) -> Path:
    cache = project_cache(project)
    marker = cache / "metadata.json"
    if marker.is_file() and not refresh:
        return cache
    if refresh and cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    files = image_files(project.images)
    records: list[dict[str, Any]] = [
        {
            "id": str(index),
            "name": path.relative_to(project.images).as_posix(),
            "path": str(path.resolve()),
        }
        for index, path in enumerate(files)
    ]
    health = [
        _image_health(path, str(index), path.relative_to(project.images).as_posix())
        for index, path in enumerate(files)
    ]
    diagnostics, cameras, assets = _reconstruction_outputs(project, cache, records)
    scene = {
        "schema_version": 1,
        "id": cache.name,
        "name": project.root.name,
        "coordinate_convention": (
            "COLMAP world coordinates; cam_from_world is applied to column vectors"
        ),
        "stats": {
            "registered_images": diagnostics["registration"]["registered_images"],
            "input_images": len(records),
            "sparse_points": diagnostics["tracks"]["points"],
            "mean_reprojection_error_px": diagnostics["reprojection_error_px"]["mean"],
        },
        "assets": assets,
        "cameras": cameras,
    }
    centers = np.asarray(diagnostics["camera_centers"], dtype=float)
    sectors: set[int] = set()
    if len(centers):
        offsets = centers[:, :2] - centers[:, :2].mean(axis=0)
        sectors = {int((np.arctan2(y, x) + np.pi) / (2 * np.pi) * 8) % 8 for x, y in offsets}
    coverage_ratio = len(sectors) / 8
    requirements = PROFILE["requirements"]
    reconstruction_checks = [
        {
            "metric": "registration_ratio",
            "value": diagnostics["registration"]["registration_ratio"],
            "operator": ">=",
            "threshold": requirements["registration_ratio_min"],
            "passed": diagnostics["registration"]["registration_ratio"]
            >= requirements["registration_ratio_min"],
        },
        {
            "metric": "reprojection_p95_px",
            "value": diagnostics["reprojection_error_px"]["p95"],
            "operator": "<=",
            "threshold": requirements["reprojection_p95_px_max"],
            "passed": diagnostics["reprojection_error_px"]["p95"]
            <= requirements["reprojection_p95_px_max"],
        },
        {
            "metric": "median_track_length",
            "value": diagnostics["tracks"]["median_length"],
            "operator": ">=",
            "threshold": requirements["median_track_length_min"],
            "passed": diagnostics["tracks"]["median_length"]
            >= requirements["median_track_length_min"],
        },
        {
            "metric": "median_view_angle_deg",
            "value": diagnostics["view_angle_degrees"]["median"],
            "operator": ">=",
            "threshold": requirements["median_view_angle_deg_min"],
            "passed": diagnostics["view_angle_degrees"]["median"]
            >= requirements["median_view_angle_deg_min"],
        },
        {
            "metric": "coverage_sector_ratio",
            "value": coverage_ratio,
            "operator": ">=",
            "threshold": requirements["coverage_sector_ratio_min"],
            "passed": coverage_ratio >= requirements["coverage_sector_ratio_min"],
        },
        {
            "metric": "dense_output",
            "value": project.dense_cloud is not None,
            "operator": "=",
            "threshold": True,
            "passed": project.dense_cloud is not None,
        },
        {
            "metric": "mesh_output",
            "value": project.mesh is not None,
            "operator": "=",
            "threshold": True,
            "passed": project.mesh is not None,
        },
    ]
    if project.sparse_model is None:
        verdict = "warning"
        findings = [
            f"Analyzed {len(records)} source images.",
            "No COLMAP sparse model was provided; reconstruction quality could not be evaluated.",
        ]
        recommendations = [
            "Provide a COLMAP sparse model to evaluate registration, reprojection, tracks, "
            "and viewing geometry."
        ]
        checks: list[dict[str, object]] = []
    else:
        checks = reconstruction_checks
        failures = [item for item in checks if not item["passed"]]
        verdict = (
            "good"
            if not failures
            else "poor"
            if diagnostics["registration"]["registration_ratio"] < 0.75
            or diagnostics["reprojection_error_px"]["p95"] > 4
            else "warning"
        )
        findings = [
            f"{diagnostics['registration']['registered_images']}/{len(records)} images registered.",
            f"P95 reprojection error is {diagnostics['reprojection_error_px']['p95']:.2f} px.",
        ]
        recommendations = (
            [
                "Maintain the current overlap and viewpoint diversity when capturing "
                "additional images."
            ]
            if not failures
            else [f"Review failed quality requirement: {item['metric']}." for item in failures]
        )
    duplicates = []
    for index, left in enumerate(health):
        for right in health[index + 1 :]:
            distance = (int(str(left["dhash"]), 16) ^ int(str(right["dhash"]), 16)).bit_count()
            if distance <= 3:
                duplicates.append(
                    {"left": left["name"], "right": right["name"], "dhash_distance": distance}
                )
    diagnostic_by_id = {str(item["image_id"]): item for item in diagnostics["per_image"]}
    for item in health:
        item["reconstruction"] = diagnostic_by_id[str(item["id"])]
    brightness = [float(item["brightness_mean"]) for item in health]
    sharpness = [float(item["blur_laplacian_variance"]) for item in health]
    assets_to_hash = {
        "sparse_cloud": cache / "sparse.ply",
        "sparse_quality": cache / "sparse-quality.bin",
        "dense_cloud": project.dense_cloud,
        "mesh": project.mesh,
    }
    provenance = {
        name: {"bytes": candidate.stat().st_size, "sha256": sha256_file(candidate)}
        for name, candidate in assets_to_hash.items()
        if candidate is not None and candidate.is_file()
    }
    report = {
        "schema_version": 1,
        "report_type": "photogrammetry-quality-report",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generator": {"name": "reconcheck", "version": "0.1.0"},
        "dataset": {
            "dataset_id": scene["id"],
            "name": scene["name"],
            "local_dataset_path": str(project.root),
            "image_count": len(records),
            "source_fingerprint_sha256": cache.name,
        },
        "quality_profile": PROFILE,
        "verdict": {
            "status": verdict,
            "checks": checks,
            "findings": findings,
            "recommendations": recommendations,
            "scope": (
                "Internal reconstruction consistency; no reference geometry was used to "
                "measure absolute geometric accuracy."
            ),
        },
        "input_image_health": {
            "image_count": len(health),
            "analysis_max_dimension_px": 256,
            "brightness_histogram": {
                "upper_bins": [0.2, 0.4, 0.6, 0.8],
                "counts": histogram(brightness, [0.2, 0.4, 0.6, 0.8]),
            },
            "sharpness_histogram": {
                "upper_bins": [0.001, 0.005, 0.02, 0.08],
                "counts": histogram(sharpness, [0.001, 0.005, 0.02, 0.08]),
            },
            "median_brightness": median(brightness),
            "median_sharpness": median(sharpness),
            "near_duplicate_pairs": duplicates,
            "per_image": health,
        },
        "reconstruction_quality": {
            "available": project.has_reconstruction,
            "scene_stats": scene["stats"],
            "diagnostics": diagnostics,
            "coverage": {
                "sector_count": 8,
                "occupied_sectors": sorted(sectors),
                "coverage_sector_ratio": coverage_ratio,
            },
        },
        "provenance": {"assets": provenance},
    }
    metadata = {
        "project": {
            "root": str(project.root),
            "images": str(project.images),
            "sparse_model": str(project.sparse_model) if project.sparse_model else None,
            "database": str(project.database) if project.database else None,
            "dense_cloud": str(project.dense_cloud) if project.dense_cloud else None,
            "mesh": str(project.mesh) if project.mesh else None,
        },
        "images": records,
    }
    for filename, payload in (
        ("manifest.json", scene),
        ("diagnostics.json", diagnostics),
        ("quality-report.json", report),
        ("metadata.json", metadata),
    ):
        (cache / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return cache


def load_metadata(cache: Path) -> dict[str, Any]:
    return json.loads((cache / "metadata.json").read_text(encoding="utf-8"))
