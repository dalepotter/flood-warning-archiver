#!/usr/bin/env python3
"""Poll the Environment Agency CAP ATOM feed and archive new CAP messages as raw XML."""

from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as ET

BASE_URL = "https://cap-xml.prd.defra.cloud/v2"
FEED_URL = f"{BASE_URL}/messages.atom"
WARNINGS_DIR = Path("warnings")

ATOM_NS = "http://www.w3.org/2005/Atom"


@dataclass(frozen=True)
class FeedEntry:
    """One entry from the ATOM index."""

    message_id: str   # e.g. "132514" (last path segment of the entry <id>)
    area_code: str    # e.g. "054WATBT1" (the entry <title>)
    updated: str      # the message's sent time, as given in the feed


def message_id_from_url(url: str) -> str:
    """'https://.../v2/message/132457' -> '132457'."""
    return url.strip().rstrip("/").rsplit("/", 1)[-1]


def parse_feed(xml_bytes: bytes) -> list[FeedEntry]:
    """Parse the ATOM index into a list of entries (order preserved, newest first)."""
    root = ET.fromstring(xml_bytes)
    entries = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        entries.append(
            FeedEntry(
                message_id=message_id_from_url(entry.findtext(f"{{{ATOM_NS}}}id", default="")),
                area_code=entry.findtext(f"{{{ATOM_NS}}}title", default="").strip(),
                updated=entry.findtext(f"{{{ATOM_NS}}}updated", default="").strip(),
            )
        )
    return entries
