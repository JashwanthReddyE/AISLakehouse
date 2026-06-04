"""Refresh the lakehouse pipeline and export fresh dashboard metrics.

Imports the latest notebooks from this repo into the Databricks workspace, runs
bronze -> silver -> gold (dark-vessel) -> gold (tanker) -> export in order, and writes
the export step's JSON to serving/metrics.json. Driven by the `databricks` CLI, which
authenticates from the DATABRICKS_HOST / DATABRICKS_TOKEN environment variables.

Used by .github/workflows/refresh.yml (and runnable locally for a manual refresh).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DBX = os.environ.get("DATABRICKS_CLI", "databricks")

# Deployment-specific resource names (not secret). Override via env if you redeploy.
STORAGE = os.environ.get("LAKE_STORAGE_ACCOUNT", "aislakestjvxguh4gld3ow")
CONTAINER = os.environ.get("LAKE_CONTAINER", "lakehouse")
EH_NS = os.environ.get("EH_NAMESPACE_FQDN", "aislake-ehns-jvxguh4gld3ow.servicebus.windows.net")
SCOPE = os.environ.get("DATABRICKS_SECRET_SCOPE", "aislakehouse")
NB_DIR = os.environ.get("DATABRICKS_NB_DIR", "/Shared/aislakehouse")

POLL_S = 20
JOB_TIMEOUT_S = 12 * 60

# Notebook (repo file -> workspace name) and the pipeline order with parameters.
NOTEBOOKS = {
    "bronze_stream": "databricks/bronze_stream.py",
    "silver_stream": "databricks/silver_stream.py",
    "gold_dark_vessel": "databricks/gold_dark_vessel.py",
    "gold_tanker_flow": "databricks/gold_tanker_flow.py",
    "export_metrics": "databricks/export_metrics.py",
}

PIPELINE = [
    ("bronze_stream", {
        "eh_namespace_fqdn": EH_NS, "eh_name": "ais-raw", "storage_account": STORAGE,
        "lake_container": CONTAINER, "secret_scope": SCOPE,
    }),
    ("silver_stream", {"storage_account": STORAGE, "lake_container": CONTAINER, "watermark": "2 hours"}),
    ("gold_dark_vessel", {
        "storage_account": STORAGE, "lake_container": CONTAINER,
        "gap_threshold_s": "1800", "normal_multiplier": "4.0",
    }),
    ("gold_tanker_flow", {
        "storage_account": STORAGE, "lake_container": CONTAINER, "fs_radius_nm": "2.0",
        "fs_min_span_hours": "1.0", "fs_max_avg_sog": "1.0", "fs_min_points": "5",
    }),
    ("export_metrics", {"storage_account": STORAGE, "lake_container": CONTAINER}),
]


def dbx(*args: str, check: bool = True) -> str:
    res = subprocess.run([DBX, *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f"databricks {' '.join(args)} failed:\n{res.stderr}\n{res.stdout}")
    return res.stdout


def import_notebooks() -> None:
    dbx("workspace", "mkdirs", NB_DIR, check=False)
    for name, rel in NOTEBOOKS.items():
        dbx("workspace", "import", f"{NB_DIR}/{name}", "--file", str(ROOT / rel),
            "--language", "PYTHON", "--format", "SOURCE", "--overwrite")
    print(f"Imported {len(NOTEBOOKS)} notebooks to {NB_DIR}")


def run_task(name: str, params: dict[str, str]) -> str:
    submit = {
        "run_name": f"refresh-{name}",
        "tasks": [{
            "task_key": name,
            "notebook_task": {"notebook_path": f"{NB_DIR}/{name}", "base_parameters": params},
        }],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(submit, f)
        spec = f.name
    try:
        run_id = json.loads(dbx("jobs", "submit", "--json", f"@{spec}", "--no-wait"))["run_id"]
    finally:
        os.unlink(spec)

    deadline = time.time() + JOB_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(POLL_S)
        run = json.loads(dbx("jobs", "get-run", str(run_id), "--output", "json"))
        life = run["state"]["life_cycle_state"]
        if life in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            result_state = run["state"].get("result_state")
            if result_state != "SUCCESS":
                sys.exit(f"Task {name} finished {result_state}: {run['state'].get('state_message')}")
            task_run_id = run["tasks"][0]["run_id"]
            out = json.loads(dbx("jobs", "get-run-output", str(task_run_id), "--output", "json"))
            result = out.get("notebook_output", {}).get("result", "")
            print(f"  {name}: SUCCESS {result}")
            return result
    sys.exit(f"Task {name} timed out after {JOB_TIMEOUT_S}s")


def main() -> None:
    import_notebooks()
    metrics = ""
    for name, params in PIPELINE:
        metrics = run_task(name, params)
    if not metrics:
        sys.exit("export_metrics produced no output")
    out = ROOT / "serving" / "metrics.json"
    out.write_text(metrics, encoding="utf-8")
    print(f"Wrote {out} ({len(metrics)} chars)")


if __name__ == "__main__":
    main()
