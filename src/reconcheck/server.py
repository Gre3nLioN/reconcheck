from __future__ import annotations

import json
import math
import mimetypes
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

import pycolmap
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reconcheck.analysis import load_metadata


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_app(cache: Path) -> FastAPI:
    cache = cache.resolve()
    metadata = load_metadata(cache)
    project = metadata["project"]
    images = metadata["images"]
    image_by_id = {str(item["id"]): Path(str(item["path"])) for item in images}
    report_jobs: set[str] = set()
    app = FastAPI(title="ReconCheck", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/dataset")
    def dataset() -> dict[str, object]:
        scene = _json(cache / "manifest.json")
        capabilities = {
            "images": "available",
            "camera_poses": f"available: {scene['stats']['registered_images']}"
            if project["sparse_model"]
            else "unavailable",
            "sparse_points": f"available: {scene['stats']['sparse_points']}"
            if project["sparse_model"]
            else "unavailable",
            "dense_points": "available" if project["dense_cloud"] else "unavailable",
            "mesh": "available" if project["mesh"] else "unavailable",
            "reference_cloud": "unavailable",
        }
        return {
            "id": scene["id"],
            "name": scene["name"],
            "image_count": len(images),
            "capabilities": capabilities,
            "images": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "width": next(
                        record["width"]
                        for record in _json(cache / "quality-report.json")["input_image_health"][
                            "per_image"
                        ]
                        if record["id"] == item["id"]
                    ),
                    "height": next(
                        record["height"]
                        for record in _json(cache / "quality-report.json")["input_image_health"][
                            "per_image"
                        ]
                        if record["id"] == item["id"]
                    ),
                    "url": f"/api/images/{item['id']}",
                }
                for item in images
            ],
        }

    @app.get("/api/scene")
    def scene() -> FileResponse:
        return FileResponse(cache / "manifest.json", media_type="application/json")

    @app.get("/api/diagnostics")
    def diagnostics() -> FileResponse:
        return FileResponse(cache / "diagnostics.json", media_type="application/json")

    @app.post("/api/quality-reports")
    def create_quality_report() -> dict[str, str]:
        job_id = uuid4().hex
        report_jobs.add(job_id)
        return {"job_id": job_id, "status_url": f"/api/quality-reports/{job_id}"}

    @app.get("/api/quality-reports/{job_id}")
    def quality_report_status(job_id: str) -> dict[str, object]:
        if job_id not in report_jobs:
            raise HTTPException(404, "quality report job not found")
        return {
            "status": "complete",
            "reused": True,
            "download_url": f"/api/quality-reports/{job_id}/download",
            "report_id": _json(cache / "manifest.json")["id"],
        }

    @app.get("/api/quality-reports/{job_id}/download")
    def download_quality_report(job_id: str) -> FileResponse:
        if job_id not in report_jobs:
            raise HTTPException(404, "quality report job not found")
        return FileResponse(
            cache / "quality-report.json",
            media_type="application/json",
            filename=f"{Path(str(project['root'])).name}-quality-report.json",
        )

    @app.get("/api/assets/{asset_name}")
    def asset(asset_name: str) -> FileResponse:
        paths = {
            "sparse.ply": cache / "sparse.ply",
            "sparse-quality.bin": cache / "sparse-quality.bin",
            "dense-cloud.ply": Path(str(project["dense_cloud"]))
            if project["dense_cloud"]
            else None,
            "mesh.ply": Path(str(project["mesh"])) if project["mesh"] else None,
        }
        path = paths.get(asset_name)
        if path is None or not path.is_file():
            raise HTTPException(404, f"asset unavailable: {asset_name}")
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/api/images/{image_id}")
    def image(image_id: str) -> FileResponse:
        path = image_by_id.get(image_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "image not found")
        return FileResponse(
            path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )

    @app.get("/api/images/{image_id}/diagnostics")
    def image_diagnostics(image_id: str, include_dense: bool = Query(False)) -> dict[str, object]:
        del include_dense
        sparse_model = project["sparse_model"]
        if not sparse_model:
            raise HTTPException(404, "no sparse reconstruction is available")
        source = next((item for item in images if str(item["id"]) == image_id), None)
        if source is None:
            raise HTTPException(404, "image not found")
        reconstruction = pycolmap.Reconstruction(Path(str(sparse_model)))
        name = str(source["name"])
        target = next(
            (
                item
                for item in reconstruction.images.values()
                if item.name == name or Path(item.name).name == Path(name).name
            ),
            None,
        )
        if target is None or not target.has_pose:
            raise HTTPException(404, "registered image not found")
        observations: list[dict[str, Any]] = []
        for point2d in target.points2D:
            if not point2d.has_point3D() or point2d.point3D_id not in reconstruction.points3D:
                continue
            point = reconstruction.points3D[point2d.point3D_id]
            projected = target.project_point(point.xyz)
            observed_x, observed_y = map(float, point2d.xy)
            projected_x, projected_y = map(float, projected)
            observations.append(
                {
                    "observed_xy": [observed_x, observed_y],
                    "projected_xy": [projected_x, projected_y],
                    "error_px": math.hypot(projected_x - observed_x, projected_y - observed_y),
                }
            )
        stride = max(1, math.ceil(len(observations) / 3000))
        errors = [float(item["error_px"]) for item in observations]
        camera = reconstruction.cameras[target.camera_id]
        return {
            "image_id": image_id,
            "width": camera.width,
            "height": camera.height,
            "observation_count": len(observations),
            "displayed_point_count": len(observations[::stride][:3000]),
            "mean_error_px": sum(errors) / len(errors) if errors else 0.0,
            "points": observations[::stride][:3000],
            "dense_points": [],
        }

    static = Path(str(files("reconcheck").joinpath("static")))
    if not (static / "index.html").is_file():
        raise RuntimeError("ReconCheck frontend assets are missing from the installation")
    app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app
