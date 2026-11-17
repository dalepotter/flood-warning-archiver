from cap_poller import FeedEntry, parse_feed


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
