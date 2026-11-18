#!/usr/bin/env python3
"""Poll the Environment Agency CAP ATOM feed and archive new CAP messages as raw XML."""

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET

BASE_URL = "https://cap-xml.prd.defra.cloud/v2"
FEED_URL = f"{BASE_URL}/messages.atom"
WARNINGS_DIR = Path("warnings")

ATOM_NS = "http://www.w3.org/2005/Atom"

USER_AGENT = "flood-warning-archiver (+https://github.com/dalepotter/flood-warning-archiver)"
REQUEST_TIMEOUT = (5, 30)  # (connect, read) seconds

# ASCII digits only: IDs become filenames, so \d (which matches other scripts' digits) is avoided.
MESSAGE_ID_PATTERN = re.compile(r"[0-9]+")

logger = logging.getLogger("cap_poller")


class FeedParseError(Exception):
    """The feed could not be read as an ATOM document."""


class FetchError(Exception):
    """A URL could not be fetched."""


@dataclass(frozen=True)
class FeedEntry:
    """One entry from the ATOM index."""

    message_id: str   # e.g. "132514" (last path segment of the entry <id>)
    area_code: str    # e.g. "054WATBT1" (the entry <title>)
    updated: str      # the message's sent time, as given in the feed


def message_id_from_url(url: str) -> str | None:
    """'https://.../v2/message/132457' -> '132457'; None if the last segment isn't numeric."""
    last_segment = url.strip().rstrip("/").rsplit("/", 1)[-1]
    return last_segment if MESSAGE_ID_PATTERN.fullmatch(last_segment) else None


def parse_feed(xml_bytes: bytes) -> list[FeedEntry]:
    """Parse the ATOM index into a list of entries (order preserved, duplicates removed)."""
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise FeedParseError(f"Feed is not valid XML: {exc}") from exc

    if root.tag != f"{{{ATOM_NS}}}feed":
        raise FeedParseError(f"Unexpected root element: {root.tag}")

    raw_entries = root.findall(f"{{{ATOM_NS}}}entry")
    entries, seen = [], set()
    for entry in raw_entries:
        raw_id = entry.findtext(f"{{{ATOM_NS}}}id", default="")
        message_id = message_id_from_url(raw_id)
        if message_id is None:
            logger.warning("Skipping feed entry with unusable id: %r", raw_id)
            continue
        if message_id in seen:
            continue
        seen.add(message_id)
        entries.append(
            FeedEntry(
                message_id=message_id,
                area_code=entry.findtext(f"{{{ATOM_NS}}}title", default="").strip(),
                updated=entry.findtext(f"{{{ATOM_NS}}}updated", default="").strip(),
            )
        )

    if raw_entries and not entries:
        raise FeedParseError("Feed has entries but none has a usable message id (format change?)")
    return entries


def message_path(warnings_dir: Path, message_id: str) -> Path:
    """Path of the archived file for a message ID. Rejects anything that isn't all digits."""
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        raise ValueError(f"Invalid message id: {message_id!r}")
    return Path(warnings_dir) / f"{message_id}.xml"


def is_stored(warnings_dir: Path, message_id: str) -> bool:
    return message_path(warnings_dir, message_id).exists()


def save_message(warnings_dir: Path, message_id: str, content: bytes) -> bool:
    """Write the raw bytes unchanged. Returns False (and writes nothing) if already stored.

    Writes to a temporary file in the same folder and renames it, so an interrupted run
    can never leave a half-written .xml file behind to be committed.
    """
    path = message_path(warnings_dir, message_id)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return True


def fetch(url: str, session: requests.Session | None = None) -> bytes:
    """GET a URL and return the body exactly as received."""
    session = session or requests.Session()
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"GET {url} failed: {exc}") from exc
    return response.content
