import pytest
import responses

from cap_poller import FEED_URL, FetchError, message_url, poll_once, save_message


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
