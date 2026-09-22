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

## Visual inspection and debugging

### Reconstruction overview

![ReconCheck reconstruction overview showing diagnostics, source images, mesh, sparse cloud, and registered cameras](docs/assets/viewer-overview.png)

Inspect registration, reprojection error, track support, viewing angles, source images, camera trajectories, sparse points, dense points, and meshes in one workspace. Diagnostic cards expand to show distributions rather than only aggregate scores.

### Quality controls

![ReconCheck layer controls showing reprojection-error coloring and geometry controls](docs/assets/quality-controls.png)

Toggle reconstruction layers and color the sparse cloud by original RGB, track support, maximum view angle, or reprojection error. Point size and opacity controls make weakly supported or high-error regions easier to locate spatially.

### Camera diagnostics

![ReconCheck camera diagnostic showing sparse observations and reprojection residuals over a source image](docs/assets/camera-diagnostics.png)

Open any registered camera to compare observed keypoints with their reprojections. Points and residual vectors are colored by error band, helping identify local alignment problems, weak image regions, and outlier observations.

The screenshots show the **Barn** training scene from [Tanks and Temples](https://www.tanksandtemples.org/). Refer to the dataset's [license terms](https://www.tanksandtemples.org/license/) for source-data usage conditions.

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
