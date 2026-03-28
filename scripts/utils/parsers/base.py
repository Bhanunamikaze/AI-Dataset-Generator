from __future__ import annotations

from typing import Any, Iterable

from ..artifacts import make_artifact


def build_unit(
    *,
    kind: str,
    source_path: str,
    title: str,
    language: str | None,
    content: str,
    metadata: dict[str, Any],
    related_paths: Iterable[str] | None = None,
    stable_payload: Any | None = None,
) -> dict[str, Any]:
    return make_artifact(
        artifact_type="unit",
        kind=kind,
        source_path=source_path,
        title=title,
        language=language,
        content=content,
        related_paths=related_paths,
        metadata=metadata,
        stable_payload=stable_payload,
    )


def build_relation(
    *,
    kind: str,
    source_path: str,
    metadata: dict[str, Any],
    related_paths: Iterable[str] | None = None,
    title: str = "",
    stable_payload: Any | None = None,
) -> dict[str, Any]:
    return make_artifact(
        artifact_type="relation",
        kind=kind,
        source_path=source_path,
        title=title,
        language=None,
        content=None,
        related_paths=related_paths,
        metadata=metadata,
        stable_payload=stable_payload,
    )


def build_bundle(
    *,
    kind: str,
    source_path: str,
    title: str,
    language: str | None,
    content: str,
    metadata: dict[str, Any],
    related_paths: Iterable[str] | None = None,
    stable_payload: Any | None = None,
) -> dict[str, Any]:
    return make_artifact(
        artifact_type="bundle",
        kind=kind,
        source_path=source_path,
        title=title,
        language=language,
        content=content,
        related_paths=related_paths,
        metadata=metadata,
        stable_payload=stable_payload,
    )
