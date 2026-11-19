#!/usr/bin/env python3
"""Poll the Environment Agency CAP ATOM feed and archive new CAP messages as raw XML."""

import argparse
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET

BASE_URL = "https://cap-xml.prd.defra.cloud/v2"
FEED_URL = f"{BASE_URL}/messages.atom"
WARNINGS_DIR = Path("warnings")
FEED_ARCHIVE_PATH = Path("feed/messages.atom")

ATOM_NS = "http://www.w3.org/2005/Atom"
CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

USER_AGENT = "flood-warning-archiver (+https://github.com/dalepotter/flood-warning-archiver)"
REQUEST_TIMEOUT = (5, 30)  # (connect, read) seconds
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# ASCII digits only: IDs become filenames, so \d (which matches other scripts' digits) is avoided.
MESSAGE_ID_PATTERN = re.compile(r"[0-9]+")

logger = logging.getLogger("cap_poller")


class FeedParseError(Exception):
    """The feed could not be read as an ATOM document."""


class FetchError(Exception):
    """A URL could not be fetched."""


class InvalidMessageError(Exception):
    """Fetched content is not a CAP 1.2 alert."""


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


def _feed_comparison_bytes_content_only(content: bytes) -> bytes:
    """Serialize a valid Atom feed with only the root feed-level updated value ignored."""
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise FeedParseError(f"Feed is not valid XML: {exc}") from exc
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise FeedParseError(f"Unexpected root element: {root.tag}")

    updated = root.find(f"{{{ATOM_NS}}}updated")
    if updated is not None:
        updated.text = ""
    return ET.tostring(root, encoding="utf-8")


def save_feed(path: Path, content: bytes) -> bool:
    """Write the index bytes unchanged, replacing any previous copy.

    Returns False (and writes nothing) if the stored copy is identical apart from the
    feed-level updated value, so git only sees meaningful changes. Uses the same atomic
    write as save_message.
    """
    path = Path(path)
    if path.exists():
        existing = path.read_bytes()
        if existing == content or (
            _feed_comparison_bytes_content_only(existing)
            == _feed_comparison_bytes_content_only(content)
        ):
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


def fetch(
    url: str,
    session: requests.Session | None = None,
    attempts: int = 3,
    backoff: float = 2.0,
) -> bytes:
    """GET a URL and return the body exactly as received, retrying transient failures."""
    session = session or requests.Session()
    problem = "no attempts made"
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        except (requests.ConnectionError, requests.Timeout) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        except requests.RequestException as exc:  # anything else is not worth retrying
            raise FetchError(f"GET {url} failed: {exc}") from exc
        else:
            if response.ok:
                return response.content
            problem = f"HTTP {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS_CODES:
                break
        if attempt < attempts:
            time.sleep(backoff * 2 ** (attempt - 1))
    raise FetchError(f"GET {url} failed after {attempt} attempt(s): {problem}")


def validate_cap_message(content: bytes) -> None:
    """Raise InvalidMessageError unless content is a well-formed CAP 1.2 <alert>.

    Deliberately shallow: it checks the document type, not the full schema, so that
    unexpected-but-genuine EA content is still archived. The bytes are never modified.
    """
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise InvalidMessageError(f"Not valid XML: {exc}") from exc
    if root.tag != f"{{{CAP_NS}}}alert":
        raise InvalidMessageError(f"Unexpected root element: {root.tag}")


def message_url(message_id: str) -> str:
    return f"{BASE_URL}/message/{message_id}"


@dataclass
class PollResult:
    new_ids: list[str] = field(default_factory=list)
    already_stored: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # message id -> reason
    unavailable_references: dict[str, str] = field(default_factory=dict)  # not a failure
    feed_changed: bool = False


def referenced_message_ids(content: bytes) -> list[str]:
    """Message numbers named in a CAP <references> element.

    references is a whitespace-separated list of 'sender,identifier,sent' triples, and the
    identifier ends in the message number ('2.49.0.1.826.1.20261101154420.132508' -> '132508').
    """
    root = ET.fromstring(content)
    text = root.findtext(f"{{{CAP_NS}}}references") or ""
    ids = []
    for triple in text.split():
        parts = triple.split(",")
        if len(parts) != 3:
            continue
        message_id = parts[1].rsplit(".", 1)[-1]
        if MESSAGE_ID_PATTERN.fullmatch(message_id) and message_id not in ids:
            ids.append(message_id)
    return ids


def poll_once(
    warnings_dir: Path = WARNINGS_DIR,
    session: requests.Session | None = None,
    feed_path: Path = FEED_ARCHIVE_PATH,
) -> PollResult:
    """Fetch the index, archive it if changed, then fetch, validate and save every message not yet archived.

    Messages named in the `references` of a newly archived message are fetched too, because
    superseded messages can leave the index while remaining retrievable by ID.
    """
    session = session or requests.Session()
    feed_bytes = fetch(FEED_URL, session)
    entries = parse_feed(feed_bytes)  # rejects a malformed feed before anything is saved
    result = PollResult()
    result.feed_changed = save_feed(feed_path, feed_bytes)
    pending = [(entry.message_id, False) for entry in entries]  # (id, found via references?)
    seen: set[str] = set()
    while pending:
        message_id, via_reference = pending.pop(0)
        if message_id in seen:
            continue
        seen.add(message_id)
        if is_stored(warnings_dir, message_id):
            if not via_reference:
                result.already_stored.append(message_id)
            continue
        try:
            content = fetch(message_url(message_id), session)
            validate_cap_message(content)
        except (FetchError, InvalidMessageError) as exc:
            if via_reference:
                logger.warning("Referenced message %s is not retrievable: %s", message_id, exc)
                result.unavailable_references[message_id] = str(exc)
            else:
                logger.error("Not archiving message %s: %s", message_id, exc)
                result.failed[message_id] = str(exc)
            continue
        save_message(warnings_dir, message_id, content)
        result.new_ids.append(message_id)
        pending.extend((ref, True) for ref in referenced_message_ids(content))
    return result


def format_summary(result: PollResult) -> str:
    lines = [
        "## CAP feed poll",
        "",
        f"- New messages archived: {len(result.new_ids)}",
        f"- Already archived: {len(result.already_stored)}",
        f"- Failed: {len(result.failed)}",
        f"- Referenced but unavailable: {len(result.unavailable_references)}",
        f"- Feed updated: {'yes' if result.feed_changed else 'no'}",
    ]
    if result.new_ids:
        lines += ["", "### New", *[f"- {message_id}" for message_id in result.new_ids]]
    if result.failed:
        lines += ["", "### Failed", *[f"- {message_id}: {reason}" for message_id, reason in result.failed.items()]]
    return "\n".join(lines) + "\n"


def write_github_outputs(result: PollResult, environ=None) -> None:
    """Expose results to GitHub Actions. Does nothing when run outside Actions."""
    environ = os.environ if environ is None else environ
    if output_path := environ.get("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"new_count={len(result.new_ids)}\n")
            f.write(f"new_ids={', '.join(result.new_ids)}\n")
            f.write(f"feed_changed={'true' if result.feed_changed else 'false'}\n")
    if summary_path := environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(format_summary(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive new EA CAP flood warning messages as raw XML.")
    parser.add_argument("--warnings-dir", type=Path, default=WARNINGS_DIR, help="archive folder (default: warnings)")
    parser.add_argument(
        "--feed-file", type=Path, default=FEED_ARCHIVE_PATH, help="archived copy of the index (default: feed/messages.atom)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        result = poll_once(args.warnings_dir, feed_path=args.feed_file)
    except (FetchError, FeedParseError) as exc:
        logger.error("Could not read the CAP feed: %s", exc)
        return 1

    write_github_outputs(result)
    logger.info(
        "Archived %d new message(s); %d already archived; %d failed",
        len(result.new_ids), len(result.already_stored), len(result.failed),
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
