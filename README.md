# ReconCheck

ReconCheck is a local photogrammetry dataset quality auditor and COLMAP reconstruction visualizer. It evaluates image health and reconstruction quality, explains problems, and exports a portable JSON quality report without uploading or modifying the source dataset.

## Install

Use `pipx` for an isolated command-line installation:

```bash
pipx install reconcheck
```

Or run it without installing:

```bash
uvx reconcheck analyze /path/to/colmap-project
```

## Analyze a project

```bash
reconcheck analyze /path/to/colmap-project
```

ReconCheck detects common COLMAP layouts automatically and opens the local viewer in the default browser. A typical project can contain:

```text
project/
├── images/
├── database.db              # optional
├── sparse/0/                # optional; binary or text COLMAP model
└── dense/
    ├── fused.ply            # optional
    └── meshed-poisson.ply   # optional
```

For non-standard layouts, provide paths explicitly:

```bash
reconcheck analyze /data/project \
  --images source-images \
  --sparse reconstruction/sparse/0 \
  --database reconstruction/database.db \
  --dense outputs/cloud.ply \
  --mesh outputs/mesh.ply
```

Paths may be absolute or relative to the project directory.

## Image-only datasets

An image directory without a COLMAP sparse model receives a partial report covering image health. The report clearly states that registration, reprojection, track support, viewing geometry, and geometric accuracy could not be evaluated.

## Quality report

The viewer's **Export quality report** action downloads a JSON report containing:

- `good`, `warning`, or `poor` verdict
- Versioned general-photogrammetry quality profile
- Registration, reprojection, track, view-angle, and coverage measurements
- Per-image sharpness, exposure, clipping, contrast, hashes, and reconstruction metrics
- Findings and corrective recommendations
- Sparse, dense, and mesh provenance hashes when available

These metrics evaluate internal consistency. Without reference geometry, they do not prove absolute geometric accuracy.

## Privacy and storage

ReconCheck runs locally, binds to `127.0.0.1` by default, and does not upload data. Source datasets are read-only. Derived scene assets and reports are cached in the operating system's standard ReconCheck application-data directory.

## Development

```bash
uv sync --dev
cd frontend
npm ci
npm run build
cd ..
rm -rf src/reconcheck/static
cp -r frontend/dist src/reconcheck/static
uv run pytest
uv run ruff check src tests
uv build
```

Use `--refresh` to rebuild a cached analysis and `--no-browser` to prevent automatic browser launch.
