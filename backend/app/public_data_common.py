from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    run_id: int
    pages: int
    records: int
    inserted: int
    updated: int
    unchanged: int
    deactivated: int


def stable_hash(payload: dict[str, Any]) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


ALLOWED_EXTERNAL_CONTENT_HOSTS = frozenset(
    {
        "www.youtube.com",
        "youtube.com",
        "youtu.be",
        "blog.naver.com",
        "happybean.naver.com",
    }
)


def canonical_content_url(value: str | None, fallback: str) -> str:
    """Keep known public content links and prevent local/unknown URLs entering the DB."""
    if not value:
        return fallback
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if host == "scllab.co.kr" or host.endswith(".scllab.co.kr"):
        return urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme == "https" and host in ALLOWED_EXTERNAL_CONTENT_HOSTS:
        return value
    return fallback
