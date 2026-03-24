from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = ROOT_DIR / "workspace"
DEFAULT_DB_PATH = WORKSPACE_DIR / "run_state.sqlite"

TASK_TYPES = ("sft", "dpo")
PIPELINE_STATUSES = ("pending", "pass", "fail", "rewrite")
RUNTIME_STATUSES = (
    "pending",
    "seeded",
    "raw_generated",
    "augmented",
    "heuristic_failed",
    "judge_pending",
    "verified_pass",
    "verified_fail",
    "deduped",
    "exported",
    "error",
)

SCHEMA_SQL = f"""
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    user_query TEXT NOT NULL,
    mode TEXT NOT NULL,
    source_type TEXT NOT NULL,
    tool_context TEXT,
    status TEXT NOT NULL DEFAULT 'initialized',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    task_type TEXT NOT NULL CHECK (task_type IN {TASK_TYPES}),
    instruction TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    response_format TEXT NOT NULL CHECK (response_format IN ('single', 'preference_pair')),
    response_text TEXT,
    response_chosen TEXT,
    response_rejected TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{{}}',
    pipeline_status TEXT NOT NULL DEFAULT 'pending' CHECK (pipeline_status IN {PIPELINE_STATUSES}),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN {RUNTIME_STATUSES}),
    source_type TEXT NOT NULL DEFAULT 'generated',
    source_uri TEXT,
    raw_payload TEXT,
    judge_score INTEGER,
    judge_reason TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (run_id)
);

CREATE INDEX IF NOT EXISTS idx_records_status ON records (status);
CREATE INDEX IF NOT EXISTS idx_records_pipeline_status ON records (pipeline_status);
CREATE INDEX IF NOT EXISTS idx_records_run_id ON records (run_id);
"""


@dataclass(slots=True)
class CanonicalRecord:
    id: str
    task_type: str
    instruction: str
    context: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    pipeline_status: str = "pending"
    status: str = "pending"
    run_id: str | None = None
    source_type: str = "generated"
    source_uri: str | None = None
    raw_payload: str | None = None
    judge_score: int | None = None
    judge_reason: str | None = None
    error_message: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_workspace() -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    ensure_workspace()
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    ensure_workspace()
    connection = get_connection(db_path)
    try:
        connection.executescript(SCHEMA_SQL)
        connection.commit()
    finally:
        connection.close()
    return Path(db_path)


def serialize_metadata(metadata: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(metadata or {}), sort_keys=True)


def flatten_record(record: CanonicalRecord | Mapping[str, Any]) -> dict[str, Any]:
    payload = record if isinstance(record, Mapping) else record.__dict__
    response = dict(payload.get("response") or {})
    row = {
        "id": payload["id"],
        "run_id": payload.get("run_id"),
        "task_type": payload["task_type"],
        "instruction": payload["instruction"],
        "context": payload.get("context", ""),
        "response_format": response.get("format", "single"),
        "response_text": response.get("text"),
        "response_chosen": response.get("chosen"),
        "response_rejected": response.get("rejected"),
        "metadata_json": serialize_metadata(payload.get("metadata")),
        "pipeline_status": payload.get("pipeline_status", "pending"),
        "status": payload.get("status", "pending"),
        "source_type": payload.get("source_type", "generated"),
        "source_uri": payload.get("source_uri"),
        "raw_payload": payload.get("raw_payload"),
        "judge_score": payload.get("judge_score"),
        "judge_reason": payload.get("judge_reason"),
        "error_message": payload.get("error_message"),
    }
    return row


def upsert_record(
    connection: sqlite3.Connection,
    record: CanonicalRecord | Mapping[str, Any],
) -> None:
    row = flatten_record(record)
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO records (
            id,
            run_id,
            task_type,
            instruction,
            context,
            response_format,
            response_text,
            response_chosen,
            response_rejected,
            metadata_json,
            pipeline_status,
            status,
            source_type,
            source_uri,
            raw_payload,
            judge_score,
            judge_reason,
            error_message,
            created_at,
            updated_at
        ) VALUES (
            :id,
            :run_id,
            :task_type,
            :instruction,
            :context,
            :response_format,
            :response_text,
            :response_chosen,
            :response_rejected,
            :metadata_json,
            :pipeline_status,
            :status,
            :source_type,
            :source_uri,
            :raw_payload,
            :judge_score,
            :judge_reason,
            :error_message,
            :created_at,
            :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            run_id = excluded.run_id,
            task_type = excluded.task_type,
            instruction = excluded.instruction,
            context = excluded.context,
            response_format = excluded.response_format,
            response_text = excluded.response_text,
            response_chosen = excluded.response_chosen,
            response_rejected = excluded.response_rejected,
            metadata_json = excluded.metadata_json,
            pipeline_status = excluded.pipeline_status,
            status = excluded.status,
            source_type = excluded.source_type,
            source_uri = excluded.source_uri,
            raw_payload = excluded.raw_payload,
            judge_score = excluded.judge_score,
            judge_reason = excluded.judge_reason,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """,
        {
            **row,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )


def fetch_records_by_status(
    connection: sqlite3.Connection,
    statuses: Iterable[str],
) -> list[sqlite3.Row]:
    values = tuple(statuses)
    placeholders = ", ".join("?" for _ in values)
    query = f"SELECT * FROM records WHERE status IN ({placeholders}) ORDER BY created_at ASC"
    return list(connection.execute(query, values))


def update_record_status(
    connection: sqlite3.Connection,
    record_id: str,
    *,
    status: str | None = None,
    pipeline_status: str | None = None,
    error_message: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if pipeline_status is not None:
        fields.append("pipeline_status = ?")
        values.append(pipeline_status)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)

    fields.append("updated_at = ?")
    values.append(utc_now())
    values.append(record_id)

    connection.execute(
        f"UPDATE records SET {', '.join(fields)} WHERE id = ?",
        values,
    )


if __name__ == "__main__":
    path = initialize_database()
    print(f"Initialized SQLite state at {path}")
