from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .files import ensure_parent_dir, write_json, write_jsonl

ROOT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = ROOT_DIR / "workspace"
INGEST_RUNS_DIR = WORKSPACE_DIR / "ingest_runs"


def build_artifact_id(artifact_type: str, payload: Any) -> str:
    material = json.dumps(
        {"artifact_type": artifact_type, "payload": payload},
        sort_keys=True,
        ensure_ascii=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"art_{digest[:16]}"


def make_artifact(
    *,
    artifact_type: str,
    kind: str,
    source_path: str,
    title: str = "",
    language: str | None = None,
    content: str | None = None,
    related_paths: Iterable[str] | None = None,
    metadata: dict[str, Any] | None = None,
    stable_payload: Any | None = None,
) -> dict[str, Any]:
    related = [str(item) for item in (related_paths or [])]
    payload = stable_payload or {
        "kind": kind,
        "source_path": source_path,
        "title": title,
        "language": language,
        "related_paths": related,
        "metadata": metadata or {},
    }
    artifact: dict[str, Any] = {
        "id": build_artifact_id(artifact_type, payload),
        "artifact_type": artifact_type,
        "kind": kind,
        "source_path": str(source_path),
        "title": title,
        "language": language,
        "content": content,
        "related_paths": related,
        "metadata": dict(metadata or {}),
    }
    return artifact


def ensure_ingest_output_dir(run_id: str, output_dir: str | Path | None = None) -> Path:
    if output_dir is None:
        destination = INGEST_RUNS_DIR / run_id
    else:
        destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def get_ingest_output_paths(output_dir: str | Path) -> dict[str, Path]:
    destination = Path(output_dir)
    ensure_parent_dir(destination / "manifest.json")
    return {
        "manifest": destination / "manifest.json",
        "files": destination / "files.jsonl",
        "units": destination / "units.jsonl",
        "relations": destination / "relations.jsonl",
        "bundles": destination / "bundles.jsonl",
        "drafts": destination / "drafts.jsonl",
        "report": destination / "ingest_report.json",
    }


def write_ingest_outputs(
    output_dir: str | Path,
    *,
    manifest: dict[str, Any],
    files: list[dict[str, Any]],
    units: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
    drafts: list[dict[str, Any]] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Path]:
    paths = get_ingest_output_paths(output_dir)
    write_json(paths["manifest"], manifest)
    write_jsonl(paths["files"], files)
    write_jsonl(paths["units"], units)
    write_jsonl(paths["relations"], relations)
    write_jsonl(paths["bundles"], bundles)
    if drafts is not None:
        write_jsonl(paths["drafts"], drafts)
    if report is not None:
        write_json(paths["report"], report)
    return paths
