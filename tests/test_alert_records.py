from pathlib import Path

import pytest
import responses

from cap_poller import (
    ALERT_FIELDS,
    FEED_URL,
    InvalidMessageError,
    PollResult,
    alert_record,
    backfill_alert_records,
    format_commit_message,
    main,
    message_url,
    poll_once,
    read_alert_records,
    save_alert_records,
    save_message,
)


def test_extracts_the_requested_fields_from_example_alert():
    path = Path(__file__).parent.parent / "warnings" / "132910.xml"
    assert alert_record("132910", path.read_bytes()) == {
        "message_id": "132910",
        "sent": "2026-11-17T07:06:44+00:00",
        "msgType": "Alert",
        "responseType": "Monitor",
        "urgency": "Immediate",
        "severity": "Minor",
        "certainty": "Likely",
        "onset": "2026-11-17T07:06:44+00:00",
        "expires": "2026-11-19T07:06:44+00:00",
        "headline": "Flooding is possible: River Cole",
        "awareness_level": "1; green; Minor",
        "awareness_type": "12; Flooding",
        "impacts": "Flooding is possible - be prepared",
        "references": "",
    }


def test_reference_ids_from_example_are_pipe_delimited():
    path = Path(__file__).parent.parent / "warnings" / "134010.xml"
    assert alert_record("134010", path.read_bytes())["references"] == (
        "132629|132752|132810|132816|132820|132831|132958|133273|133504|133616|133690|133865"
    )


def test_reference_ids_are_deduplicated_in_source_order_even_without_info():
    raw = (
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><references>'
        b's,prefix.2,t s,prefix.1,t s,prefix.2,t'
        b'</references></alert>'
    )
    assert alert_record("3", raw)["references"] == "2|1"


def test_missing_fields_are_empty_and_missing_info_is_logged(caplog):
    record = alert_record("1", b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>')
    assert record == {name: "1" if name == "message_id" else "" for name in ALERT_FIELDS}
    assert "no info block" in caplog.text


def test_repeated_values_and_csv_escaping(tmp_path):
    raw = (
        '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><info>'
        '<responseType>Monitor</responseType><responseType>Avoid</responseType>'
        '<headline>River, "Cole"\nCaf\u00e9</headline>'
        '<parameter><valueName>impacts</valueName><value>First</value></parameter>'
        '<parameter><valueName>impacts</valueName><value>Second</value></parameter>'
        '</info></alert>'
    ).encode()
    record = alert_record("1", raw)
    assert record["responseType"] == "Monitor; Avoid"
    assert record["impacts"] == "First; Second"
    path = tmp_path / "alerts.csv"
    save_alert_records(path, {"1": record})
    assert read_alert_records(path) == {"1": record}


def test_multiple_info_blocks_are_rejected():
    raw = b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><info/><info/></alert>'
    with pytest.raises(InvalidMessageError, match="multiple info blocks"):
        alert_record("1", raw)


def test_csv_is_sorted_and_identical_records_are_not_rewritten(tmp_path, alert_xml):
    path = tmp_path / "nested" / "alerts.csv"
    records = {key: alert_record(key, alert_xml) for key in ("10", "2", "1")}
    assert save_alert_records(path, records) is True
    assert list(read_alert_records(path)) == ["1", "2", "10"]
    mtime = path.stat().st_mtime_ns
    assert save_alert_records(path, records) is False
    assert path.stat().st_mtime_ns == mtime


def test_csv_replacement_failure_preserves_previous_file(tmp_path, alert_xml, monkeypatch):
    path = tmp_path / "alerts.csv"
    save_alert_records(path, {})
    original = path.read_bytes()

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("cap_poller.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        save_alert_records(path, {"1": alert_record("1", alert_xml)})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "content",
    ["wrong,header\n", ",".join(ALERT_FIELDS) + "\n1,missing\n"],
)
def test_invalid_existing_csv_is_rejected(tmp_path, content):
    path = tmp_path / "alerts.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_alert_records(path)


def test_duplicate_csv_message_ids_are_rejected(tmp_path, alert_xml):
    path = tmp_path / "alerts.csv"
    save_alert_records(path, {"1": alert_record("1", alert_xml)})
    content = path.read_text()
    row = content.partition("\n")[2]
    path.write_text(content + row)
    with pytest.raises(ValueError, match="Duplicate message id"):
        read_alert_records(path)


@responses.activate
def test_csv_extraction_failure_is_reported_but_raw_xml_is_archived(make_feed, warnings_dir, tmp_path):
    raw = b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><info/><info/></alert>'
    responses.add(responses.GET, FEED_URL, body=make_feed(["1"]))
    responses.add(responses.GET, message_url("1"), body=raw)
    path = tmp_path / "alerts.csv"
    result = poll_once(warnings_dir, csv_path=path)
    assert "multiple info blocks" in result.failed["1"]
    assert (warnings_dir / "1.xml").read_bytes() == raw
    assert read_alert_records(path) == {}


@responses.activate
def test_poll_records_new_referenced_and_updated_messages(make_feed, alert_xml, cancel_xml, warnings_dir, tmp_path):
    path = tmp_path / "alerts.csv"
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    assert poll_once(warnings_dir, csv_path=path).csv_changed is True
    assert set(read_alert_records(path)) == {"132514", "132508"}
    changed = cancel_xml.replace(b"</headline>", b" Updated</headline>")
    assert changed != cancel_xml
    responses.replace(responses.GET, message_url("132514"), body=changed)
    result = poll_once(warnings_dir, csv_path=path)
    assert result.updated_ids == ["132514"]
    assert result.csv_changed is True
    assert read_alert_records(path)["132514"] == alert_record("132514", changed)
    assert read_alert_records(path)["132508"] == alert_record("132508", alert_xml)
    assert poll_once(warnings_dir, csv_path=path).csv_changed is False


@responses.activate
def test_unchanged_xml_recovers_missing_csv_row(make_feed, alert_xml, warnings_dir, tmp_path):
    save_message(warnings_dir, "132508", alert_xml)
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    path = tmp_path / "alerts.csv"
    result = poll_once(warnings_dir, csv_path=path)
    assert result.new_ids == result.updated_ids == []
    assert result.csv_changed is True
    assert read_alert_records(path)["132508"] == alert_record("132508", alert_xml)
    assert format_commit_message(PollResult(csv_changed=True)) == "data: Update CAP alert records"


@responses.activate
def test_failed_fetch_preserves_existing_record_and_records_other_messages(
    make_feed, alert_xml, warnings_dir, tmp_path
):
    path = tmp_path / "alerts.csv"
    record = alert_record("1", alert_xml)
    save_alert_records(path, {"1": record})
    responses.add(responses.GET, FEED_URL, body=make_feed(["1", "2"]))
    responses.add(responses.GET, message_url("1"), status=404)
    responses.add(responses.GET, message_url("2"), body=alert_xml)
    result = poll_once(warnings_dir, csv_path=path)
    assert "1" in result.failed
    assert read_alert_records(path) == {"1": record, "2": alert_record("2", alert_xml)}


@responses.activate
def test_backfill_uses_local_files_and_is_idempotent(alert_xml, cancel_xml, warnings_dir, tmp_path):
    save_message(warnings_dir, "132508", alert_xml)
    save_message(warnings_dir, "132514", cancel_xml)
    (warnings_dir / ".gitkeep").touch()
    path = tmp_path / "alerts.csv"
    assert main(["--backfill-csv", "--warnings-dir", str(warnings_dir), "--csv-file", str(path)]) == 0
    assert read_alert_records(path) == {
        "132508": alert_record("132508", alert_xml),
        "132514": alert_record("132514", cancel_xml),
    }
    mtime = path.stat().st_mtime_ns
    assert backfill_alert_records(warnings_dir, path) == 2
    assert path.stat().st_mtime_ns == mtime
    assert len(responses.calls) == 0


def test_invalid_archive_does_not_replace_csv(alert_xml, warnings_dir, tmp_path):
    save_message(warnings_dir, "1", alert_xml)
    save_message(warnings_dir, "2", b"<html/>")
    path = tmp_path / "alerts.csv"
    save_alert_records(path, {})
    original = path.read_bytes()
    assert main(["--backfill-csv", "--warnings-dir", str(warnings_dir), "--csv-file", str(path)]) == 1
    assert path.read_bytes() == original
