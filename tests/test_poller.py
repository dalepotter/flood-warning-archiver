import pytest
import responses

from cap_poller import FEED_URL, FeedParseError, FetchError, message_url, poll_once, save_feed, save_message


@responses.activate
def test_poll_once_archives_new_messages_as_raw_xml(make_feed, alert_xml, cancel_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514", "132508"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    result = poll_once(warnings_dir)

    assert sorted(result.new_ids) == ["132508", "132514"]
    assert (warnings_dir / "132508.xml").read_bytes() == alert_xml
    assert (warnings_dir / "132514.xml").read_bytes() == cancel_xml


@responses.activate
def test_poll_once_does_not_refetch_archived_messages(make_feed, alert_xml, warnings_dir):
    save_message(warnings_dir, "132508", alert_xml)
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))

    result = poll_once(warnings_dir)

    assert result.new_ids == []
    assert result.already_stored == ["132508"]
    assert len(responses.calls) == 1  # the index only


@responses.activate
def test_second_poll_with_unchanged_feed_is_a_no_op(make_feed, alert_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    poll_once(warnings_dir)
    second = poll_once(warnings_dir)

    assert second.new_ids == []
    assert len(responses.calls) == 3  # index, message, index


@responses.activate
def test_only_the_new_message_is_fetched_when_the_feed_grows(make_feed, alert_xml, cancel_xml, warnings_dir):
    save_message(warnings_dir, "132508", alert_xml)
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514", "132508"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)

    result = poll_once(warnings_dir)

    assert result.new_ids == ["132514"]
    assert [c.request.url for c in responses.calls] == [FEED_URL, message_url("132514")]


@responses.activate
def test_a_failing_message_does_not_stop_the_others(make_feed, alert_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514", "132508"]))
    responses.add(responses.GET, message_url("132514"), status=500)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    result = poll_once(warnings_dir)

    assert result.new_ids == ["132508"]
    assert list(result.failed) == ["132514"]
    assert not (warnings_dir / "132514.xml").exists()


@responses.activate
def test_a_non_cap_200_response_is_not_archived(make_feed, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))
    responses.add(responses.GET, message_url("132514"), body=b"<html>Service unavailable</html>")

    result = poll_once(warnings_dir)

    assert "132514" in result.failed
    assert list(warnings_dir.glob("*")) == []


@responses.activate
def test_a_failed_message_is_retried_on_the_next_poll(make_feed, alert_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), status=404)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    assert "132508" in poll_once(warnings_dir).failed
    assert poll_once(warnings_dir).new_ids == ["132508"]


@responses.activate
def test_index_failure_propagates(warnings_dir):
    responses.add(responses.GET, FEED_URL, status=503)
    with pytest.raises(FetchError):
        poll_once(warnings_dir)


@responses.activate
def test_empty_feed_archives_nothing(make_feed, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed([]))
    result = poll_once(warnings_dir)
    assert (result.new_ids, result.already_stored, result.failed) == ([], [], {})


@responses.activate
def test_empty_message_body_is_not_archived(make_feed, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["1"]))
    responses.add(responses.GET, message_url("1"), body=b"")
    assert "1" in poll_once(warnings_dir).failed


@responses.activate
def test_message_bytes_are_archived_exactly_even_with_a_non_utf8_declaration(make_feed, warnings_dir):
    raw = (
        b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n'
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><note>caf\xe9</note></alert>\r\n'
    )
    responses.add(responses.GET, FEED_URL, body=make_feed(["1"]))
    responses.add(responses.GET, message_url("1"), body=raw)
    poll_once(warnings_dir)
    assert (warnings_dir / "1.xml").read_bytes() == raw


@responses.activate
def test_message_listed_twice_in_the_feed_is_fetched_once(make_feed, alert_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508", "132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    result = poll_once(warnings_dir)
    assert result.new_ids == ["132508"]
    assert len(responses.calls) == 2


@responses.activate
def test_unrelated_files_in_the_archive_folder_are_ignored(make_feed, alert_xml, warnings_dir):
    warnings_dir.mkdir()
    (warnings_dir / ".gitkeep").write_text("")
    (warnings_dir / "notes.txt").write_text("hello")
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    assert poll_once(warnings_dir).new_ids == ["132508"]


@responses.activate
def test_referenced_message_missing_from_the_feed_is_archived(make_feed, alert_xml, cancel_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))  # the Cancel only
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)  # referenced by the Cancel

    result = poll_once(warnings_dir)

    assert sorted(result.new_ids) == ["132508", "132514"]
    assert (warnings_dir / "132508.xml").read_bytes() == alert_xml


@responses.activate
def test_unretrievable_reference_is_recorded_but_is_not_a_failure(make_feed, cancel_xml, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)
    responses.add(responses.GET, message_url("132508"), status=404)

    result = poll_once(warnings_dir)

    assert result.new_ids == ["132514"]
    assert "132508" in result.unavailable_references
    assert result.failed == {}


@responses.activate
def test_already_archived_reference_is_not_refetched_or_listed_as_in_the_feed(
    make_feed, alert_xml, cancel_xml, warnings_dir
):
    save_message(warnings_dir, "132508", alert_xml)
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)

    result = poll_once(warnings_dir)

    assert result.new_ids == ["132514"]
    assert result.already_stored == []
    assert [c.request.url for c in responses.calls] == [FEED_URL, message_url("132514")]


@responses.activate
def test_reference_cycles_terminate(make_feed, warnings_dir):
    a = (
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><references>'
        b"s,2.49.0.1.826.1.20261101000000.2,2026-11-01T00:00:00+00:00</references></alert>"
    )
    b = (
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><references>'
        b"s,2.49.0.1.826.1.20261101000000.1,2026-11-01T00:00:00+00:00</references></alert>"
    )
    responses.add(responses.GET, FEED_URL, body=make_feed(["1"]))
    responses.add(responses.GET, message_url("1"), body=a)
    responses.add(responses.GET, message_url("2"), body=b)

    assert sorted(poll_once(warnings_dir).new_ids) == ["1", "2"]


@responses.activate
def test_poll_once_archives_the_feed_as_received(make_feed, tmp_path):
    feed = make_feed([])
    feed_path = tmp_path / "feed" / "messages.atom"
    responses.add(responses.GET, FEED_URL, body=feed)

    result = poll_once(tmp_path / "w", feed_path=feed_path)

    assert result.feed_changed is True
    assert feed_path.read_bytes() == feed


@responses.activate
def test_unchanged_feed_is_not_rewritten(make_feed, tmp_path):
    feed_path = tmp_path / "messages.atom"
    responses.add(responses.GET, FEED_URL, body=make_feed([]))

    poll_once(tmp_path / "w", feed_path=feed_path)
    mtime = feed_path.stat().st_mtime_ns
    second = poll_once(tmp_path / "w", feed_path=feed_path)

    assert second.feed_changed is False
    assert feed_path.stat().st_mtime_ns == mtime


def test_feed_level_updated_change_is_not_archived(tmp_path):
    feed_path = tmp_path / "messages.atom"
    original = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Flood warnings</title>'
        b"<updated>2026-11-18T22:34:21.410Z</updated></feed>"
    )
    updated = original.replace(b"2026-11-18T22:34:21.410Z", b"2026-11-19T04:07:00.704Z")
    save_feed(feed_path, original)
    mtime = feed_path.stat().st_mtime_ns

    assert save_feed(feed_path, updated) is False
    assert feed_path.read_bytes() == original
    assert feed_path.stat().st_mtime_ns == mtime


def test_entry_updated_change_replaces_the_archived_feed(tmp_path):
    feed_path = tmp_path / "messages.atom"
    original = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b"<updated>2026-11-18T22:34:21.410Z</updated></entry></feed>"
    )
    updated = original.replace(b"2026-11-18T22:34:21.410Z", b"2026-11-19T04:07:00.704Z")
    save_feed(feed_path, original)

    assert save_feed(feed_path, updated) is True
    assert feed_path.read_bytes() == updated


@responses.activate
def test_changed_feed_replaces_the_archived_copy(make_feed, alert_xml, tmp_path):
    feed_path = tmp_path / "messages.atom"
    responses.add(responses.GET, FEED_URL, body=make_feed([]))
    poll_once(tmp_path / "w", feed_path=feed_path)
    responses.replace(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    result = poll_once(tmp_path / "w", feed_path=feed_path)

    assert result.feed_changed is True
    assert feed_path.read_bytes() == make_feed(["132508"])


@responses.activate
def test_malformed_feed_is_not_archived(tmp_path):
    feed_path = tmp_path / "messages.atom"
    responses.add(responses.GET, FEED_URL, body=b"<html>Forbidden</html>")

    with pytest.raises(FeedParseError):
        poll_once(tmp_path / "w", feed_path=feed_path)

    assert not feed_path.exists()


def test_save_feed_leaves_no_temporary_files(tmp_path):
    save_feed(tmp_path / "feed" / "messages.atom", b"<feed/>")
    assert [p.name for p in (tmp_path / "feed").iterdir()] == ["messages.atom"]
