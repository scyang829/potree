"""
One-stop tool: upload a LAS/LAZ file, auto-process it (ground classification +
PotreeConverter), and serve it through the potree viewer for lane digitizing.
Also exposes a GeoJSON -> Shapefile export endpoint.

Run with:
    python webapp/server.py
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
POINTCLOUDS_DIR = REPO_ROOT / "pointclouds"
POTREE_CONVERTER = REPO_ROOT / "PotreeConverter" / "PotreeConverter"

app = FastAPI()


def sanitize_name(filename: str) -> str:
	stem = Path(filename).stem
	stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_") or "dataset"
	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	return f"{stem}_{timestamp}"


def get_proj4(las_path: Path) -> tuple[str, str]:
	result = subprocess.run(
		["pdal", "info", "--metadata", str(las_path)],
		capture_output=True, text=True, check=True,
	)
	metadata = json.loads(result.stdout)["metadata"]
	srs = metadata.get("srs", {})
	proj4 = srs.get("proj4")
	epsg = None
	for component in srs.get("json", {}).get("components", [srs.get("json", {})]):
		code = component.get("id", {}).get("code")
		if code:
			epsg = f"EPSG:{code}"
			break
	if not proj4:
		raise HTTPException(400, "Could not determine the coordinate system (SRS) of the uploaded file.")
	return epsg or "EPSG:UNKNOWN", proj4


def is_already_classified(las_path: Path) -> bool:
	result = subprocess.run(
		["pdal", "info", "--stats", "--dimensions", "Classification", str(las_path)],
		capture_output=True, text=True, check=True,
	)
	stats = json.loads(result.stdout)["stats"]["statistic"][0]
	return stats["maximum"] > 0


def run_ground_classification(las_path: Path, out_path: Path):
	pipeline = {
		"pipeline": [
			{"type": "readers.las", "filename": str(las_path)},
			{"type": "filters.smrf"},
			{"type": "writers.las", "filename": str(out_path), "minor_version": 2, "dataformat_id": 3},
		]
	}
	with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
		json.dump(pipeline, f)
		pipeline_path = f.name
	subprocess.run(["pdal", "pipeline", pipeline_path], check=True, capture_output=True, text=True)


@app.post("/api/process")
async def process_pointcloud(file: UploadFile = File(...)):
	suffix = Path(file.filename).suffix.lower()
	if suffix not in (".las", ".laz"):
		raise HTTPException(400, "Only .las/.laz files are supported. PCD is not supported yet "
			"(ego-frame PCD data lacks absolute coordinates without vehicle pose).")

	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		uploaded_path = tmp_path / file.filename
		with open(uploaded_path, "wb") as f:
			shutil.copyfileobj(file.file, f)

		try:
			epsg, proj4 = get_proj4(uploaded_path)
		except (subprocess.CalledProcessError, HTTPException) as e:
			detail = e.detail if isinstance(e, HTTPException) else e.stderr
			raise HTTPException(400, f"Failed to read file metadata: {detail}")

		if is_already_classified(uploaded_path):
			source_for_conversion = uploaded_path
		else:
			classified_path = tmp_path / "classified.las"
			try:
				run_ground_classification(uploaded_path, classified_path)
			except subprocess.CalledProcessError as e:
				raise HTTPException(500, f"Ground classification failed: {e.stderr}")
			source_for_conversion = classified_path

		dataset_id = sanitize_name(file.filename)
		out_dir = POINTCLOUDS_DIR / dataset_id
		out_dir.mkdir(parents=True, exist_ok=True)

		# The bundled binary needs its bundled liblaszip.so (unversioned name), which isn't on the
		# system loader path by default even when a system liblaszip is installed under a versioned name.
		env = {**os.environ, "LD_LIBRARY_PATH": str(POTREE_CONVERTER.parent)}
		result = subprocess.run(
			[str(POTREE_CONVERTER), str(source_for_conversion), "-o", str(out_dir), "--projection", epsg],
			capture_output=True, text=True, env=env,
		)
		if result.returncode != 0:
			shutil.rmtree(out_dir, ignore_errors=True)
			raise HTTPException(500, f"PotreeConverter failed: {result.stderr}")

		meta = {"epsg": epsg, "proj4": proj4, "sourceFile": file.filename}
		(out_dir / "potree_meta.json").write_text(json.dumps(meta, indent=2))

	return JSONResponse({"dataset": dataset_id})


@app.post("/api/export-shp")
async def export_shp(request: Request, epsg: str | None = None):
	geojson_text = (await request.body()).decode("utf-8")

	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		geojson_path = tmp_path / "measure.geojson"
		geojson_path.write_text(geojson_text)

		shp_path = tmp_path / "measure.shp"
		# The GeoJSON coordinates are raw scene coordinates (already in the pointcloud's
		# real-world CRS, e.g. UTM meters), not WGS84 lon/lat as GeoJSON normally implies.
		# -a_srs assigns the correct CRS without reprojecting the (already-correct) values.
		cmd = ["ogr2ogr", "-f", "ESRI Shapefile"]
		if epsg:
			cmd += ["-a_srs", epsg]
		cmd += [str(shp_path), str(geojson_path)]
		result = subprocess.run(cmd, capture_output=True, text=True)
		if result.returncode != 0:
			raise HTTPException(500, f"SHP conversion failed: {result.stderr}")

		zip_path = tmp_path / "measure_shp.zip"
		with zipfile.ZipFile(zip_path, "w") as zf:
			for component in tmp_path.glob("measure.*"):
				if component.suffix != ".geojson" and component.name != zip_path.name:
					zf.write(component, component.name)

		final_zip = Path(tempfile.mkstemp(suffix=".zip")[1])
		shutil.copy(zip_path, final_zip)

	return FileResponse(final_zip, media_type="application/zip", filename="measure_shp.zip")


@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
	return (REPO_ROOT / "webapp" / "upload.html").read_text()


# Must be registered last: this mount is a catch-all for everything else
# (examples/, build/, libs/, resources/, pointclouds/).
app.mount("/", StaticFiles(directory=str(REPO_ROOT), html=True), name="static")


if __name__ == "__main__":
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8080)
