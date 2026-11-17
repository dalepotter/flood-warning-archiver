import logging

import pytest

from cap_poller import FeedEntry, FeedParseError, message_id_from_url, parse_feed

ATOM_OPEN = '<feed xmlns="http://www.w3.org/2005/Atom">'


def test_parse_feed_returns_one_entry_per_atom_entry(feed_xml):
    assert len(parse_feed(feed_xml)) == 3


def test_parse_feed_extracts_message_ids_in_feed_order(feed_xml):
    assert [e.message_id for e in parse_feed(feed_xml)] == ["132514", "132508", "132457"]


def test_parse_feed_extracts_area_code_from_title(feed_xml):
    assert parse_feed(feed_xml)[0].area_code == "054WATBT1"


def test_parse_feed_extracts_sent_time(feed_xml):
    assert parse_feed(feed_xml)[0].updated == "2026-11-02T09:35:32.000Z"


def test_parse_feed_returns_feed_entries(feed_xml):
    assert isinstance(parse_feed(feed_xml)[0], FeedEntry)


def test_parse_feed_with_no_entries_returns_empty_list():
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>'
    assert parse_feed(xml) == []


def feed_with_ids(*ids: str) -> bytes:
    entries = "".join(f"<entry><title>A</title><id>{i}</id></entry>" for i in ids)
    return f"{ATOM_OPEN}{entries}</feed>".encode()


def test_malformed_xml_raises_feed_parse_error():
    with pytest.raises(FeedParseError):
        parse_feed(b"<feed><entry>")


def test_non_atom_document_raises_feed_parse_error():
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>Forbidden</body></html>")


def test_empty_body_raises_feed_parse_error():
    with pytest.raises(FeedParseError):
        parse_feed(b"")


def test_xml_entity_declarations_are_rejected():
    xml = (
        b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "x">]>'
        b'<feed xmlns="http://www.w3.org/2005/Atom">&a;</feed>'
    )
    with pytest.raises(FeedParseError):
        parse_feed(xml)


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "https://x/v2/message/12a", "https://x/v2/message/", "", "https://x/v2/message/١٢٣"],
)
def test_message_id_from_url_rejects_non_numeric_ids(bad_id):
    assert message_id_from_url(bad_id) is None


def test_unusable_entries_are_skipped_and_logged(caplog):
    xml = feed_with_ids("https://x/v2/message/1", "https://x/v2/message/../etc", "https://x/v2/message/2")
    with caplog.at_level(logging.WARNING):
        entries = parse_feed(xml)
    assert [e.message_id for e in entries] == ["1", "2"]
    assert "unusable id" in caplog.text


def test_duplicate_ids_are_returned_once_in_first_seen_order():
    xml = feed_with_ids("https://x/v2/message/2", "https://x/v2/message/1", "https://x/v2/message/2")
    assert [e.message_id for e in parse_feed(xml)] == ["2", "1"]


def test_feed_where_every_entry_is_unusable_raises():
    with pytest.raises(FeedParseError):
        parse_feed(feed_with_ids("nonsense", "also/nonsense"))
