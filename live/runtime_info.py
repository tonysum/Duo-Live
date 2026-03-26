"""Deployment and process uptime helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEPLOYED_AT_PATH = Path("data/deployed_at.txt")


def read_deployed_at(path: Path = DEPLOYED_AT_PATH) -> datetime | None:
    """Parse ``data/deployed_at.txt`` (single line ISO-8601 UTC, e.g. ``2026-03-26T12:00:00Z``)."""
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except (OSError, IndexError, ValueError) as e:
        logger.debug("No valid deployed_at: %s", e)
        return None


def human_uptime_cn(total_seconds: int) -> str:
    """Human-readable duration (e.g. ``3天5小时12分``)."""
    if total_seconds < 0:
        total_seconds = 0
    if total_seconds < 60:
        return f"{total_seconds}秒"
    d, rem = divmod(total_seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _s = divmod(rem, 60)
    parts: list[str] = []
    if d:
        parts.append(f"{d}天")
    if h or d:
        parts.append(f"{h}小时")
    if m or h or d:
        parts.append(f"{m}分")
    return "".join(parts)
