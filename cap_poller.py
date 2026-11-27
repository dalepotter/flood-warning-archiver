#!/usr/bin/env python3
"""Poll the Environment Agency CAP ATOM feed and archive new CAP messages as raw XML."""

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET

BASE_URL = "https://cap-xml.prd.defra.cloud/v2"
FEED_URL = f"{BASE_URL}/messages.atom"
WARNINGS_DIR = Path("warnings")
FEED_ARCHIVE_PATH = Path("feed/messages.atom")
ALERTS_CSV_PATH = Path("alerts.csv")
ALERT_CHAINS_PATH = Path("alert-chains.yml")
ALERT_FIELDS = [
    "message_id", "sent", "msgType", "responseType", "urgency", "severity", "certainty",
    "onset", "expires", "headline", "awareness_level", "awareness_type", "impacts", "references",
]

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
    """Write the raw bytes unchanged if new or different; return whether a write occurred.

    Writes to a temporary file in the same folder and renames it, so an interrupted run
    can never leave a half-written .xml file behind to be committed.
    """
    path = message_path(warnings_dir, message_id)
    if path.exists() and path.read_bytes() == content:
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


def alert_record(message_id: str, content: bytes) -> dict[str, str]:
    """Extract a CSV row without modifying the source XML."""
    message_path(WARNINGS_DIR, message_id)
    validate_cap_message(content)
    root = ET.fromstring(content)
    record = dict.fromkeys(ALERT_FIELDS, "")
    record["message_id"] = message_id
    record["references"] = "|".join(referenced_message_ids(content))
    for name in ("sent", "msgType"):
        record[name] = root.findtext(f"{{{CAP_NS}}}{name}", default="")
    infos = root.findall(f"{{{CAP_NS}}}info")
    if len(infos) > 1:
        raise InvalidMessageError(f"Message {message_id} has multiple info blocks; cannot represent as one CSV row")
    if not infos:
        logger.warning("Message %s has no info block; CSV info fields will be empty", message_id)
        return record
    info = infos[0]
    for name in ("urgency", "severity", "certainty", "onset", "expires", "headline"):
        record[name] = info.findtext(f"{{{CAP_NS}}}{name}", default="")
    for name in ("responseType", "awareness_level", "awareness_type", "impacts"):
        if name == "responseType":
            values = [element.text or "" for element in info.findall(f"{{{CAP_NS}}}responseType")]
        else:
            values = [
                parameter.findtext(f"{{{CAP_NS}}}value", default="")
                for parameter in info.findall(f"{{{CAP_NS}}}parameter")
                if parameter.findtext(f"{{{CAP_NS}}}valueName") == name
            ]
        record[name] = "; ".join(values)
    return record


def read_alert_records(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    records = {}
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != ALERT_FIELDS:
            raise ValueError(f"Unexpected CSV header in {path}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Invalid CSV row in {path} at line {reader.line_num}")
            message_id = row["message_id"]
            message_path(WARNINGS_DIR, message_id)
            if message_id in records:
                raise ValueError(f"Duplicate message id {message_id} in {path}")
            records[message_id] = row
    return records


def save_alert_records(path: Path, records: dict[str, dict[str, str]]) -> bool:
    """Write a deterministic UTF-8 CSV atomically, only when its bytes change."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ALERT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records[key] for key in sorted(records, key=lambda key: (int(key), key)))
    content = output.getvalue().encode("utf-8")
    if path.exists() and path.read_bytes() == content:
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


# Strings that YAML reads back unchanged without quotes (file names, msgType values).
PLAIN_YAML_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
HEADLINE_LEVELS = {"Flooding is possible": "possible", "Flooding is expected": "expected"}


def _headline_parts(headline: str) -> tuple[str, str]:
    """'Flooding is possible: River Cole' -> ('possible', 'River Cole')."""
    prefix, separator, location = headline.partition(": ")
    if not separator:
        return "unknown", headline
    return HEADLINE_LEVELS.get(prefix, "unknown"), location


def _yaml_string(value: str) -> str:
    """A YAML scalar that loads back as exactly this string (JSON strings are valid YAML)."""
    return value if PLAIN_YAML_PATTERN.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def _sent_time(value: str) -> datetime | None:
    """Parse a CAP sent time; None unless it is ISO 8601 with a UTC offset, as CAP requires."""
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _reference_ids(record: dict[str, str]) -> list[str]:
    return [ref for ref in record["references"].split("|") if ref]


def _elapsed(start: datetime | None, end: datetime | None) -> tuple[str, str]:
    """Time from start to end as YAML scalars ('<d>d <h>h', hours), rounded to the nearest hour.

    Both are 'null' if either time is unusable.
    """
    if start is None or end is None:
        return "null", "null"
    hours = round((end - start).total_seconds() / 3600)
    return f"{hours // 24}d {hours % 24}h", str(hours)


def _chain_issues(
    chain: list[dict[str, str]], sent: list[datetime | None], levels: list[str], locations: list[str]
) -> list[str]:
    """Reconciliation tags for one chain (oldest message first). Order is fixed."""
    types = [record["msgType"] for record in chain]
    ids = [record["message_id"] for record in chain]
    issues = []
    if types[0] != "Alert":
        issues.append("root_not_alert")
    if any(_reference_ids(record) != ids[:n] for n, record in enumerate(chain)):
        issues.append("reference_mismatch")
    if None in sent:
        issues.append("invalid_sent")
    if "Cancel" in types[:-1] and any(t != "Cancel" for t in types[types.index("Cancel"):]):
        issues.append("update_after_cancel")
    if any(a == b == "Cancel" for a, b in zip(types, types[1:])):
        issues.append("duplicate_cancel")
    if len(set(levels)) > 1:
        issues.append("level_change")
    if len(set(locations)) > 1:
        issues.append("location_change")
    if types[-1] != "Cancel":
        issues.append("no_cancel")
    return issues


def format_alert_chains(records: dict[str, dict[str, str]], source: str = "alerts.csv") -> str:
    """Group alert records into chains and render them as deterministic YAML.

    A message belongs to the chain named by the first ID in its references (or its own ID if it
    has none). Chains and their messages are ordered by numeric message ID, like alerts.csv.
    Written by hand rather than with a YAML library so the runtime needs no extra dependency
    and each message stays on one line.
    """
    chains: dict[str, list[dict[str, str]]] = {}
    for record in records.values():
        references = _reference_ids(record)
        chains.setdefault(references[0] if references else record["message_id"], []).append(record)

    lines = [
        f"# Generated from {source}; chains and messages in alerts.csv order (ascending message ID).",
        f"source: {_yaml_string(source)}",
        f"message_count: {len(records)}",
        f"chain_count: {len(chains)}",
        "chains:" if chains else "chains: []",
    ]
    by_id = lambda record: int(record["message_id"])  # noqa: E731
    for chain in sorted(chains.values(), key=lambda chain: min(map(by_id, chain))):
        chain.sort(key=by_id)
        sent = [_sent_time(record["sent"]) for record in chain]
        sent_text = [record["sent"] if when else "null" for record, when in zip(chain, sent)]
        levels, locations = zip(*(_headline_parts(record["headline"]) for record in chain))
        first, last = chain[0], chain[-1]
        duration, duration_hours = _elapsed(sent[0], sent[-1])
        lines += [
            f"  - location: {json.dumps(sorted(locations)[0], ensure_ascii=False)}",
            f"    alert_id: {first['message_id']}",
            f"    alert_sent: {sent_text[0]}",
            f"    status: {'closed' if last['msgType'] == 'Cancel' else 'open'}",
            f"    latest_id: {last['message_id']}",
            f"    latest_sent: {sent_text[-1]}",
            f"    duration: {duration}",
            f"    duration_hours: {duration_hours}",
            f"    issues: [{', '.join(_chain_issues(chain, sent, levels, locations))}]",
            "    messages:",
        ]
        for record, when, text, level in zip(chain, sent, sent_text, levels):
            elapsed, elapsed_hours = _elapsed(sent[0], when)
            lines.append(
                f"      - {{id: {record['message_id']}, type: {_yaml_string(record['msgType'])}, sent: {text}, "
                f"elapsed: {elapsed}, elapsed_hours: {elapsed_hours}, level: {level}}}"
            )
    return "\n".join(lines) + "\n"


def save_alert_chains(path: Path, records: dict[str, dict[str, str]], source: str = "alerts.csv") -> bool:
    """Regenerate the alert chains file atomically, only when its bytes change."""
    content = format_alert_chains(records, source).encode("utf-8")
    path = Path(path)
    if path.exists() and path.read_bytes() == content:
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


def backfill_alert_records(warnings_dir: Path, csv_path: Path, chains_path: Path = ALERT_CHAINS_PATH) -> int:
    """Rebuild the CSV and alert chains from local archived messages using the polling extractor."""
    if not warnings_dir.is_dir():
        raise ValueError(f"Archive directory does not exist: {warnings_dir}")
    records = {}
    for path in sorted(warnings_dir.glob("*.xml")):
        if not MESSAGE_ID_PATTERN.fullmatch(path.stem):
            logger.warning("Skipping archive file with unusable message id: %s", path)
            continue
        records[path.stem] = alert_record(path.stem, path.read_bytes())
    save_alert_records(csv_path, records)
    save_alert_chains(chains_path, records, Path(csv_path).name)
    return len(records)


def message_url(message_id: str) -> str:
    return f"{BASE_URL}/message/{message_id}"


@dataclass
class PollResult:
    new_ids: list[str] = field(default_factory=list)
    updated_ids: list[str] = field(default_factory=list)
    already_stored: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # message id -> reason
    unavailable_references: dict[str, str] = field(default_factory=dict)  # not a failure
    feed_changed: bool = False
    csv_changed: bool = False
    chains_changed: bool = False


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
    csv_path: Path = ALERTS_CSV_PATH,
    chains_path: Path = ALERT_CHAINS_PATH,
) -> PollResult:
    """Fetch the index, archive it if changed, then reconcile every listed message.

    Messages named in the `references` of a new or changed message are fetched too, because
    superseded messages can leave the index while remaining retrievable by ID.
    """
    session = session or requests.Session()
    records = read_alert_records(csv_path)
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
        stored = is_stored(warnings_dir, message_id)
        if stored and via_reference:
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
        try:
            record = alert_record(message_id, content)
        except InvalidMessageError as exc:
            logger.error("Could not record message %s in CSV: %s", message_id, exc)
            result.failed[message_id] = str(exc)
            record = None
        if save_message(warnings_dir, message_id, content):
            if stored:
                result.updated_ids.append(message_id)
            else:
                result.new_ids.append(message_id)
            pending.extend((ref, True) for ref in referenced_message_ids(content))
        elif not via_reference:
            result.already_stored.append(message_id)
        if record is not None:
            records[message_id] = record
    result.csv_changed = save_alert_records(csv_path, records)
    result.chains_changed = save_alert_chains(chains_path, records, Path(csv_path).name)
    return result


def format_summary(result: PollResult) -> str:
    lines = [
        "## CAP feed poll",
        "",
        f"- New messages archived: {len(result.new_ids)}",
        f"- Existing messages updated: {len(result.updated_ids)}",
        f"- Already archived: {len(result.already_stored)}",
        f"- Failed: {len(result.failed)}",
        f"- Referenced but unavailable: {len(result.unavailable_references)}",
        f"- Feed updated: {'yes' if result.feed_changed else 'no'}",
        f"- Alerts CSV updated: {'yes' if result.csv_changed else 'no'}",
        f"- Alert chains updated: {'yes' if result.chains_changed else 'no'}",
    ]
    if result.new_ids:
        lines += ["", "### New", *[f"- {message_id}" for message_id in result.new_ids]]
    if result.updated_ids:
        lines += ["", "### Updated", *[f"- {message_id}" for message_id in result.updated_ids]]
    if result.failed:
        lines += ["", "### Failed", *[f"- {message_id}: {reason}" for message_id, reason in result.failed.items()]]
    return "\n".join(lines) + "\n"


def format_commit_message(result: PollResult) -> str:
    """Build a concise commit title and include message IDs when useful."""
    categories = []
    if result.new_ids:
        categories.append((len(result.new_ids), "new"))
    if result.updated_ids:
        categories.append((len(result.updated_ids), "updated"))

    if len(categories) == 2:
        title = (
            f"data: Archive {categories[0][0]} new and "
            f"{categories[1][0]} updated CAP messages"
        )
    elif result.new_ids:
        if len(result.new_ids) == 1:
            title = f"data: Archive CAP message {result.new_ids[0]}"
        else:
            title = f"data: Archive {len(result.new_ids)} new CAP messages"
    elif result.updated_ids:
        if len(result.updated_ids) == 1:
            title = f"data: Update CAP message {result.updated_ids[0]}"
        else:
            title = f"data: Update {len(result.updated_ids)} CAP messages"
    elif result.feed_changed:
        title = "data: Update CAP feed index"
    elif result.csv_changed:
        title = "data: Update CAP alert records"
    elif result.chains_changed:
        title = "data: Update alert chains"
    else:
        title = "data: Poll CAP feed"

    details = []
    if result.new_ids and len(result.new_ids) > 1:
        details.append(f"New: {', '.join(result.new_ids)}")
    if result.updated_ids and len(result.updated_ids) > 1:
        details.append(f"Updated: {', '.join(result.updated_ids)}")
    if result.new_ids and result.updated_ids:
        details = [
            f"New: {', '.join(result.new_ids)}",
            f"Updated: {', '.join(result.updated_ids)}",
        ]
    return f"{title}\n\n" + "\n".join(details) if details else title


def write_github_outputs(result: PollResult, environ=None) -> None:
    """Expose results to GitHub Actions. Does nothing when run outside Actions."""
    environ = os.environ if environ is None else environ
    if output_path := environ.get("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"new_count={len(result.new_ids)}\n")
            f.write(f"new_ids={', '.join(result.new_ids)}\n")
            f.write(f"updated_count={len(result.updated_ids)}\n")
            f.write(f"updated_ids={', '.join(result.updated_ids)}\n")
            f.write(f"feed_changed={'true' if result.feed_changed else 'false'}\n")
            f.write("commit_message<<EOF\n")
            f.write(f"{format_commit_message(result)}\n")
            f.write("EOF\n")
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
    parser.add_argument("--csv-file", type=Path, default=ALERTS_CSV_PATH, help="alert records CSV (default: alerts.csv)")
    parser.add_argument(
        "--chains-file", type=Path, default=ALERT_CHAINS_PATH, help="alert chains YAML (default: alert-chains.yml)"
    )
    parser.add_argument(
        "--backfill-csv", action="store_true",
        help="rebuild CSV and alert chains from archived XML without fetching the feed",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.backfill_csv:
        try:
            count = backfill_alert_records(args.warnings_dir, args.csv_file, args.chains_file)
        except (OSError, ValueError, InvalidMessageError, csv.Error) as exc:
            logger.error("Could not backfill alerts CSV: %s", exc)
            return 1
        logger.info("Recorded %d archived message(s) in %s and %s", count, args.csv_file, args.chains_file)
        return 0

    try:
        result = poll_once(
            args.warnings_dir, feed_path=args.feed_file, csv_path=args.csv_file, chains_path=args.chains_file
        )
    except (FetchError, FeedParseError) as exc:
        logger.error("Could not read the CAP feed: %s", exc)
        return 1
    except (OSError, ValueError, csv.Error) as exc:
        logger.error("Could not maintain the CAP archive: %s", exc)
        return 1

    write_github_outputs(result)
    logger.info(
        "Archived %d new message(s), updated %d existing message(s); %d unchanged; %d failed",
        len(result.new_ids), len(result.updated_ids), len(result.already_stored), len(result.failed),
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
