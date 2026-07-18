"""Dated, immutable snapshot writer per phase1-data-platform-design.md §4.

Layout:
  data/snapshots/YYYY-MM-DD/raw/{source}/{table}.parquet   # exactly as pulled
  data/snapshots/YYYY-MM-DD/manifest.json
  data/schemas/{table}/v1.json

Nothing under a dated snapshot is ever edited after the fact -- corrections
are a new snapshot, not an in-place fix. A GOLD marker is deliberately NOT
written by this job: GOLD means "all validation stages (§6) passed", and only
a lightweight Stage-1-style sanity check (row-count floors) is implemented so
far. Full schema/semantic/statistical validation is future Phase 1 work.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

from capture.config import LEAGUE_TIMEZONE, SCHEMA_ROOT, SNAPSHOT_ROOT


def today_snapshot_date() -> str:
    """Today's date in the league's timezone (ET) -- not the machine/CI runner's UTC date."""
    return datetime.now(LEAGUE_TIMEZONE).strftime("%Y-%m-%d")


def snapshot_dir(date: str) -> Path:
    return Path(SNAPSHOT_ROOT) / date


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except Exception:
        return None


class SnapshotWriter:
    """Accumulates raw tables for one dated snapshot, then writes the manifest."""

    def __init__(self, date: str):
        self.date = date
        self.dir = snapshot_dir(date)
        self.raw_dir = self.dir / "raw"
        self._files: list[dict] = []
        self._checks: list[dict] = []

    def already_captured(self) -> bool:
        return (self.dir / "manifest.json").exists()

    def write_table(
        self, source: str, table: str, df: pd.DataFrame, schema_version: str = "v1"
    ) -> Path:
        out_dir = self.raw_dir / source
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{table}.parquet"
        df.to_parquet(out_path, index=False)

        self._files.append(
            {
                "source": source,
                "table": table,
                "path": str(out_path.relative_to(Path("."))),
                "row_count": len(df),
                "sha256": _sha256(out_path),
                "schema_version": schema_version,
                "pulled_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
            }
        )
        return out_path

    def record_check(self, name: str, passed: bool, detail: str = "") -> None:
        self._checks.append({"check": name, "passed": passed, "detail": detail})

    def all_checks_passed(self) -> bool:
        return all(c["passed"] for c in self._checks)

    def write_schema(self, table: str, columns: dict[str, str], version: str = "v1") -> None:
        """columns: {column_name: human-readable type/description}"""
        schema_dir = Path(SCHEMA_ROOT) / table
        schema_dir.mkdir(parents=True, exist_ok=True)
        path = schema_dir / f"{version}.json"
        if not path.exists():
            path.write_text(json.dumps({"table": table, "version": version, "columns": columns}, indent=2))

    def finalize(self, source_endpoints: dict[str, str]) -> Path:
        all_passed = self.all_checks_passed()
        manifest = {
            "snapshot_date": self.date,
            "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
            "code_git_commit": _git_commit(),
            "source_endpoints": source_endpoints,
            "files": self._files,
            "validation": {
                "stage": "stage1_lite (row-count sanity only; stage2/3 not yet implemented)",
                "checks": self._checks,
                "all_passed": all_passed,
            },
        }
        manifest_path = self.dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return manifest_path
