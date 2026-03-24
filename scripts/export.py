from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.utils.canonical import row_to_record
from scripts.utils.db import fetch_records_by_status, get_connection, initialize_database
from scripts.utils.files import write_csv, write_json, write_jsonl

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FLAT_SCHEMA = ROOT_DIR / "resources" / "target-schemas" / "csv_columns.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export verified dataset records into JSONL and CSV targets."
    )
    parser.add_argument(
        "--format",
        choices=("openai", "huggingface", "csv", "jsonl", "all"),
        default="openai",
        help="Export target format.",
    )
    parser.add_argument(
        "--from-status",
        action="append",
        default=[],
        help="Statuses to export from SQLite. Repeatable.",
    )
    parser.add_argument("--source-run-id", help="Filter export to a specific run id.")
    parser.add_argument(
        "--split",
        type=float,
        default=0.1,
        help="Holdout fraction for the test split. Default: 0.1",
    )
    parser.add_argument("--seed", type=int, default=42, help="Shuffle seed for dataset splitting.")
    parser.add_argument(
        "--output-dir",
        default="workspace",
        help="Directory for exported files and generated data card.",
    )
    parser.add_argument(
        "--schema-file",
        help="Optional flat target schema file for csv/jsonl exports.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Optional path to the SQLite database. Defaults to workspace/run_state.sqlite.",
    )
    parser.add_argument("--report", help="Optional path to write a JSON summary report.")
    return parser.parse_args()


def split_records(records: list[dict[str, Any]], split_ratio: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shuffled = list(records)
    random.Random(seed).shuffle(shuffled)
    test_count = int(len(shuffled) * split_ratio)
    if split_ratio > 0 and len(shuffled) > 1 and test_count == 0:
        test_count = 1
    test_records = shuffled[:test_count]
    train_records = shuffled[test_count:]
    return train_records, test_records


def to_openai_record(record: dict[str, Any]) -> dict[str, Any]:
    response = record["response"]
    if response["format"] == "single":
        messages = []
        if record["context"]:
            messages.append({"role": "system", "content": record["context"]})
        messages.append({"role": "user", "content": record["instruction"]})
        messages.append({"role": "assistant", "content": response["text"]})
        return {"messages": messages, "metadata": record["metadata"]}

    return {
        "input": {
            "instruction": record["instruction"],
            "context": record["context"],
        },
        "chosen": response["chosen"],
        "rejected": response["rejected"],
        "metadata": record["metadata"],
    }


def to_huggingface_record(record: dict[str, Any]) -> dict[str, Any]:
    response = record["response"]
    if response["format"] == "single":
        return {
            "messages": [
                {"role": "system", "content": record["context"]},
                {"role": "user", "content": record["instruction"]},
                {"role": "assistant", "content": response["text"]},
            ],
            "metadata": record["metadata"],
        }
    return {
        "prompt": record["instruction"],
        "context": record["context"],
        "chosen": response["chosen"],
        "rejected": response["rejected"],
        "metadata": record["metadata"],
    }


def to_csv_row(record: dict[str, Any]) -> dict[str, Any]:
    response = record["response"]
    return {
        "id": record["id"],
        "task_type": record["task_type"],
        "instruction": record["instruction"],
        "context": record["context"],
        "response_text": response.get("text", ""),
        "chosen": response.get("chosen", ""),
        "rejected": response.get("rejected", ""),
        "difficulty": record["metadata"].get("difficulty", ""),
        "persona": record["metadata"].get("persona", ""),
        "source_type": record.get("source_type", ""),
        "judge_score": record.get("judge_score", ""),
        "judge_reason": record.get("judge_reason", ""),
    }


def load_flat_schema(path: str | None) -> dict[str, Any]:
    schema_path = Path(path) if path else DEFAULT_FLAT_SCHEMA
    with open(schema_path, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    if schema.get("mode") != "flat":
        raise ValueError(f"Unsupported flat export schema mode: {schema.get('mode')}")
    return schema


def resolve_source(record: dict[str, Any], source: str) -> Any:
    current: Any = record
    for part in source.split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    if isinstance(current, (dict, list)):
        return json.dumps(current, ensure_ascii=True)
    return current


def to_flat_row(record: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {
        column["name"]: resolve_source(record, column["source"])
        for column in schema["columns"]
    }


def write_data_card(
    path: Path,
    records: list[dict[str, Any]],
    split_ratio: float,
    export_format: str,
    schema_file: str | None,
) -> Path:
    task_counts = Counter(record["task_type"] for record in records)
    source_counts = Counter(record.get("source_type", "unknown") for record in records)
    lines = [
        "# Dataset Card",
        "",
        "## Summary",
        f"- Total records: {len(records)}",
        f"- Split ratio (test): {split_ratio}",
        f"- Export format: {export_format}",
        f"- Flat schema file: {schema_file or str(DEFAULT_FLAT_SCHEMA)}",
        f"- Task type distribution: {dict(task_counts)}",
        f"- Source type distribution: {dict(source_counts)}",
        "",
        "## Generation Method",
        "- Tool-native reasoning in Codex, Antigravity, or Claude Code.",
        "- Deterministic local scripts for normalization, verification, deduplication, and export.",
        "",
        "## Known Limits",
        "- Review-file judgments are only present when an IDE-agent or user supplies them.",
        "- Web-derived datasets depend on the evidence collected during the run.",
        "",
        "## License",
        "- Set by the dataset author or downstream project requirements.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_jsonl_pair(
    output_dir: Path,
    prefix: str,
    train_records: list[dict[str, Any]],
    test_records: list[dict[str, Any]],
) -> list[str]:
    written: list[str] = []
    train_path = output_dir / f"{prefix}_train.jsonl"
    write_jsonl(train_path, train_records)
    written.append(str(train_path))
    if test_records:
        test_path = output_dir / f"{prefix}_test.jsonl"
        write_jsonl(test_path, test_records)
        written.append(str(test_path))
    return written


def export_csv_pair(
    output_dir: Path,
    prefix: str,
    train_records: list[dict[str, Any]],
    test_records: list[dict[str, Any]],
    *,
    fieldnames: list[str],
) -> list[str]:
    written: list[str] = []
    train_path = output_dir / f"{prefix}_train.csv"
    write_csv(train_path, train_records, fieldnames=fieldnames)
    written.append(str(train_path))
    if test_records:
        test_path = output_dir / f"{prefix}_test.csv"
        write_csv(test_path, test_records, fieldnames=fieldnames)
        written.append(str(test_path))
    return written


def export_flat_jsonl_pair(
    output_dir: Path,
    prefix: str,
    train_records: list[dict[str, Any]],
    test_records: list[dict[str, Any]],
) -> list[str]:
    written: list[str] = []
    train_path = output_dir / f"{prefix}_train.jsonl"
    write_jsonl(train_path, train_records)
    written.append(str(train_path))
    if test_records:
        test_path = output_dir / f"{prefix}_test.jsonl"
        write_jsonl(test_path, test_records)
        written.append(str(test_path))
    return written


def main() -> None:
    args = parse_args()
    db_path = initialize_database(args.db) if args.db else initialize_database()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = get_connection(db_path)
    try:
        statuses = tuple(args.from_status or ["verified_pass"])
        rows = fetch_records_by_status(connection, statuses)
        if args.source_run_id:
            rows = [row for row in rows if row["run_id"] == args.source_run_id]
        records = [row_to_record(row) for row in rows if row["pipeline_status"] == "pass"]
    finally:
        connection.close()

    train_records, test_records = split_records(records, args.split, args.seed)
    written_files: list[str] = []
    flat_schema = load_flat_schema(args.schema_file)
    flat_train = [to_flat_row(record, flat_schema) for record in train_records]
    flat_test = [to_flat_row(record, flat_schema) for record in test_records]
    flat_fieldnames = [column["name"] for column in flat_schema["columns"]]

    if args.format in ("openai", "all"):
        written_files.extend(
            export_jsonl_pair(
                output_dir,
                "openai",
                [to_openai_record(record) for record in train_records],
                [to_openai_record(record) for record in test_records],
            )
        )

    if args.format in ("huggingface", "all"):
        written_files.extend(
            export_jsonl_pair(
                output_dir,
                "huggingface",
                [to_huggingface_record(record) for record in train_records],
                [to_huggingface_record(record) for record in test_records],
            )
        )

    if args.format in ("csv", "all"):
        written_files.extend(
            export_csv_pair(
                output_dir,
                "dataset",
                flat_train,
                flat_test,
                fieldnames=flat_fieldnames,
            )
        )

    if args.format in ("jsonl", "all"):
        written_files.extend(
            export_flat_jsonl_pair(
                output_dir,
                "flat",
                flat_train,
                flat_test,
            )
        )

    data_card_path = write_data_card(
        output_dir / "DATA_CARD.md",
        records,
        args.split,
        args.format,
        args.schema_file,
    )
    summary = {
        "db_path": str(db_path),
        "records_exported": len(records),
        "train_count": len(train_records),
        "test_count": len(test_records),
        "format": args.format,
        "files": written_files + [str(data_card_path)],
    }
    if args.report:
        write_json(args.report, summary)

    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
