from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_PAGE_PATH = re.compile(r"/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*/?")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_INDEX_STATES = frozenset({"noindex", "queued", "indexed"})


def canonical_page_path(slug: str) -> str:
    if slug == "/":
        return "/"
    if not isinstance(slug, str) or not _PAGE_PATH.fullmatch(slug):
        raise ValueError("Build page metadata snapshot is invalid")
    return f"/{slug.strip('/')}/"


def validate_page_metadata_snapshot(
    metadata: object, *, expected_slugs: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    if not isinstance(metadata, list):
        raise ValueError("Build page metadata snapshot is invalid")
    by_slug: dict[str, dict[str, Any]] = {}
    required = {"slug", "path", "index_state", "thin", "content_chars", "hash"}
    optional = {"source_hash", "promoted_at"}
    for item in metadata:
        if not isinstance(item, dict) or (
            set(item) != required and set(item) != required | optional
        ):
            raise ValueError("Build page metadata snapshot is invalid")
        slug = item["slug"]
        path = item["path"]
        index_state = item["index_state"]
        thin = item["thin"]
        content_chars = item["content_chars"]
        digest = item["hash"]
        source_hash = item.get("source_hash")
        promoted_at = item.get("promoted_at")
        if promoted_at is not None:
            try:
                datetime.fromisoformat(promoted_at)
            except (TypeError, ValueError) as exc:
                raise ValueError("Build page metadata snapshot is invalid") from exc
        if "source_hash" in item and (
            not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash)
        ):
            raise ValueError("Build page metadata snapshot is invalid")
        if "source_hash" not in item and promoted_at is not None:
            raise ValueError("Build page metadata snapshot is invalid")
        if index_state != "indexed" and promoted_at is not None:
            raise ValueError("Build page metadata snapshot is invalid")
        if (
            not isinstance(slug, str)
            or slug in by_slug
            or canonical_page_path(slug) != path
            or index_state not in _INDEX_STATES
            or not isinstance(thin, bool)
            or not isinstance(content_chars, int)
            or isinstance(content_chars, bool)
            or content_chars < 0
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or (thin and index_state != "noindex")
        ):
            raise ValueError("Build page metadata snapshot is invalid")
        by_slug[slug] = item
    if expected_slugs is not None and set(by_slug) != expected_slugs:
        raise ValueError("Build manifest and page metadata snapshots do not match")
    return by_slug
