from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from threading import Timer

import uvicorn

from reconcheck.analysis import analyze_project
from reconcheck.project import ProjectError, discover_project
from reconcheck.server import create_app


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="reconcheck",
        description="Evaluate and visualize photogrammetry datasets and COLMAP reconstructions.",
    )
    root.add_argument("--version", action="version", version="ReconCheck 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="Analyze a dataset and open its local viewer")
    analyze.add_argument("path", type=Path, help="Dataset or COLMAP project directory")
    analyze.add_argument(
        "--images", type=Path, help="Image directory, absolute or relative to the project"
    )
    analyze.add_argument("--sparse", type=Path, help="COLMAP sparse model directory")
    analyze.add_argument("--database", type=Path, help="COLMAP database.db")
    analyze.add_argument("--dense", type=Path, help="Dense point-cloud PLY")
    analyze.add_argument("--mesh", type=Path, help="Mesh PLY")
    analyze.add_argument("--refresh", action="store_true", help="Rebuild cached analysis")
    analyze.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically"
    )
    analyze.add_argument(
        "--host", default="127.0.0.1", help="Viewer bind address (default: 127.0.0.1)"
    )
    analyze.add_argument("--port", type=int, default=8000, help="Viewer port (default: 8000)")
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command != "analyze":
        return 2
    try:
        project = discover_project(
            arguments.path,
            images=arguments.images,
            sparse=arguments.sparse,
            database=arguments.database,
            dense=arguments.dense,
            mesh=arguments.mesh,
        )
        print(f"Analyzing {project.root}")
        print(f"Images: {project.images}")
        print(f"Sparse model: {project.sparse_model or 'not provided (partial report)'}")
        cache = analyze_project(project, refresh=arguments.refresh)
        app = create_app(cache)
    except (ProjectError, RuntimeError, OSError, ValueError) as error:
        print(f"reconcheck: {error}", file=sys.stderr)
        return 1
    url_host = "127.0.0.1" if arguments.host in {"0.0.0.0", "::"} else arguments.host
    url = f"http://{url_host}:{arguments.port}"
    print(f"Quality report and viewer ready: {url}")
    print(f"Cache: {cache}")
    if not arguments.no_browser:
        Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
