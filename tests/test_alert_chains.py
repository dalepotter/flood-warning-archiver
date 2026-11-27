from pathlib import Path

import pytest
import responses
import yaml

from cap_poller import (
    ALERT_FIELDS,
    FEED_URL,
    PollResult,
    alert_record,
    backfill_alert_records,
    format_alert_chains,
    format_commit_message,
    format_summary,
    main,
    message_url,
    poll_once,
    read_alert_records,
    save_alert_chains,
    save_message,
)

REPO = Path(__file__).parent.parent


def rec(message_id, msg_type, sent, headline="Flooding is possible: River Cole", refs=()):
    record = dict.fromkeys(ALERT_FIELDS, "")
    record.update(
        message_id=str(message_id), msgType=msg_type, sent=sent, headline=headline,
        references="|".join(str(r) for r in refs),
    )
    return record


def records_of(*rows):
    return {row["message_id"]: row for row in rows}


def chains_of(*rows):
    return yaml.safe_load(format_alert_chains(records_of(*rows)))["chains"]


def test_layout_is_one_flow_mapping_per_message():
    text = format_alert_chains(records_of(
        rec(1, "Alert", "2026-11-16T16:00:53+00:00"),
        rec(2, "Cancel", "2026-11-16T20:59:44+00:00", refs=[1]),
    ))
    assert text == (
        "# Generated from alerts.csv; chains and messages in alerts.csv order (ascending message ID).\n"
        "source: alerts.csv\n"
        "message_count: 2\n"
        "chain_count: 1\n"
        "chains:\n"
        '  - location: "River Cole"\n'
        "    alert_id: 1\n"
        "    alert_sent: 2026-11-16T16:00:53+00:00\n"
        "    status: closed\n"
        "    latest_id: 2\n"
        "    latest_sent: 2026-11-16T20:59:44+00:00\n"
        "    duration: 0d 5h\n"
        "    duration_hours: 5\n"
        "    issues: []\n"
        "    messages:\n"
        "      - {id: 1, type: Alert, sent: 2026-11-16T16:00:53+00:00, elapsed: 0d 0h, elapsed_hours: 0, level: possible}\n"
        "      - {id: 2, type: Cancel, sent: 2026-11-16T20:59:44+00:00, elapsed: 0d 5h, elapsed_hours: 5, level: possible}\n"
    )


def test_source_names_the_csv():
    text = format_alert_chains({}, source="other.csv")
    assert yaml.safe_load(text) == {"source": "other.csv", "message_count": 0, "chain_count": 0, "chains": []}


def test_lone_alert_is_an_open_chain():
    [chain] = chains_of(rec(5, "Alert", "2026-11-26T16:25:27+00:00"))
    assert chain["status"] == "open"
    assert chain["latest_id"] == 5
    assert chain["duration"] == "0d 0h"
    assert chain["issues"] == ["no_cancel"]


def test_elapsed_rounds_to_nearest_hour_and_rolls_into_days():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-12T08:00:00+00:00"),
        rec(2, "Update", "2026-11-12T08:29:59+00:00", refs=[1]),
        rec(3, "Update", "2026-11-12T08:30:01+00:00", refs=[1, 2]),
        rec(4, "Cancel", "2026-11-25T09:00:00+00:00", refs=[1, 2, 3]),
    )
    assert [(m["elapsed"], m["elapsed_hours"]) for m in chain["messages"]] == [
        ("0d 0h", 0), ("0d 0h", 0), ("0d 1h", 1), ("13d 1h", 313),
    ]
    assert (chain["duration"], chain["duration_hours"]) == ("13d 1h", 313)


def test_chains_and_messages_follow_message_id_order_whatever_the_input_order():
    chains = chains_of(
        rec(30, "Cancel", "2026-11-03T00:00:00+00:00", refs=[10]),
        rec(20, "Alert", "2026-11-02T00:00:00+00:00"),
        rec(10, "Alert", "2026-11-01T00:00:00+00:00"),
        rec(15, "Update", "2026-11-01T12:00:00+00:00", refs=[10]),
    )
    assert [c["alert_id"] for c in chains] == [10, 20]
    assert [m["id"] for m in chains[0]["messages"]] == [10, 15, 30]


def test_message_ids_sort_numerically():
    chains = chains_of(rec(9, "Alert", "2026-11-01T00:00:00+00:00"), rec(10, "Alert", "2026-11-02T00:00:00+00:00"))
    assert [c["alert_id"] for c in chains] == [9, 10]


def test_messages_join_the_chain_named_by_their_first_reference():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-01T00:00:00+00:00"),
        rec(2, "Update", "2026-11-01T01:00:00+00:00", refs=[1]),
        rec(3, "Update", "2026-11-01T02:00:00+00:00", refs=[1, 2]),
    )
    assert [m["id"] for m in chain["messages"]] == [1, 2, 3]
    assert chain["issues"] == ["no_cancel"]


def test_timestamps_and_ids_load_as_native_types():
    [chain] = chains_of(rec(1, "Alert", "2026-11-01T00:00:00+00:00"))
    assert type(chain["alert_id"]) is int
    assert chain["alert_sent"].isoformat() == "2026-11-01T00:00:00+00:00"


def test_location_and_level_come_from_the_headline():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-01T00:00:00+00:00", headline="Flooding is expected: River Hayle"),
        rec(2, "Update", "2026-11-01T01:00:00+00:00", headline="Flooding is possible: River Hayle", refs=[1]),
    )
    assert chain["location"] == "River Hayle"
    assert [m["level"] for m in chain["messages"]] == ["expected", "possible"]
    assert chain["issues"] == ["level_change", "no_cancel"]


def test_unrecognised_headlines_do_not_stop_the_chain_being_built():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-01T00:00:00+00:00", headline="Severe flooding: Somewhere"),
        rec(2, "Cancel", "2026-11-01T01:00:00+00:00", headline="No prefix here", refs=[1]),
    )
    assert [m["level"] for m in chain["messages"]] == ["unknown", "unknown"]
    assert chain["location"] == "No prefix here"
    assert chain["issues"] == ["location_change"]


@pytest.mark.parametrize("location", ['Quote " and: colon', "Back\\slash", "Ystradgynlais – Afon Tawe", "#hash", "- dash"])
def test_awkward_locations_round_trip(location):
    [chain] = chains_of(rec(1, "Alert", "2026-11-01T00:00:00+00:00", headline=f"Flooding is possible: {location}"))
    assert chain["location"] == location


def test_cancel_closes_the_chain_and_reopening_is_flagged():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-24T11:00:00+00:00"),
        rec(2, "Cancel", "2026-11-25T21:00:00+00:00", refs=[1]),
        rec(3, "Update", "2026-11-26T13:00:00+00:00", refs=[1, 2]),
    )
    assert chain["status"] == "open"
    assert chain["issues"] == ["update_after_cancel", "no_cancel"]


def test_reopened_then_cancelled_again_is_closed_but_flagged():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-17T05:00:00+00:00"),
        rec(2, "Cancel", "2026-11-18T09:00:00+00:00", refs=[1]),
        rec(3, "Update", "2026-11-18T18:00:00+00:00", refs=[1, 2]),
        rec(4, "Cancel", "2026-11-19T10:00:00+00:00", refs=[1, 2, 3]),
    )
    assert chain["status"] == "closed"
    assert chain["issues"] == ["update_after_cancel"]


def test_consecutive_cancels_are_flagged():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-17T07:00:00+00:00"),
        rec(2, "Cancel", "2026-11-22T15:15:00+00:00", refs=[1]),
        rec(3, "Cancel", "2026-11-22T15:17:00+00:00", refs=[1, 2]),
    )
    assert chain["status"] == "closed"
    assert chain["issues"] == ["duplicate_cancel"]


def test_references_that_skip_earlier_chain_messages_are_flagged():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-01T00:00:00+00:00"),
        rec(2, "Update", "2026-11-01T01:00:00+00:00", refs=[1]),
        rec(3, "Cancel", "2026-11-01T02:00:00+00:00", refs=[1]),
    )
    assert chain["issues"] == ["reference_mismatch"]


def test_chain_whose_alert_was_never_archived_starts_at_its_earliest_message():
    [chain] = chains_of(
        rec(8, "Update", "2026-11-01T01:00:00+00:00", refs=[7]),
        rec(9, "Cancel", "2026-11-01T03:00:00+00:00", refs=[7, 8]),
    )
    assert chain["alert_id"] == 8
    assert chain["alert_sent"].isoformat() == "2026-11-01T01:00:00+00:00"
    assert chain["duration"] == "0d 2h"
    assert chain["issues"] == ["root_not_alert", "reference_mismatch"]


def test_missing_or_unusable_sent_times_are_flagged_not_fatal():
    [chain] = chains_of(
        rec(1, "Alert", "2026-11-01T00:00:00+00:00"),
        rec(2, "Update", "", refs=[1]),
        rec(3, "Update", "2026-11-01T02:00:00", refs=[1, 2]),  # no UTC offset
        rec(4, "Cancel", "not a time", refs=[1, 2, 3]),
    )
    assert [(m["sent"], m["elapsed"], m["elapsed_hours"]) for m in chain["messages"]] == [
        (chain["alert_sent"], "0d 0h", 0), (None, None, None), (None, None, None), (None, None, None),
    ]
    assert chain["latest_sent"] is None
    assert (chain["duration"], chain["duration_hours"]) == (None, None)
    assert chain["status"] == "closed"
    assert chain["issues"] == ["invalid_sent"]


def test_chain_with_no_usable_alert_time_has_no_elapsed_times():
    [chain] = chains_of(rec(1, "Alert", ""), rec(2, "Update", "2026-11-01T02:00:00+00:00", refs=[1]))
    assert chain["alert_sent"] is None
    assert chain["latest_sent"].isoformat() == "2026-11-01T02:00:00+00:00"
    assert [m["elapsed"] for m in chain["messages"]] == [None, None]
    assert chain["issues"] == ["invalid_sent", "no_cancel"]


def test_blank_message_type_is_kept_as_a_string():
    [chain] = chains_of(rec(1, "", "2026-11-01T00:00:00+00:00"))
    assert chain["messages"][0]["type"] == ""
    assert chain["issues"] == ["root_not_alert", "no_cancel"]


def test_save_writes_only_when_content_changes(tmp_path):
    path = tmp_path / "nested" / "alert-chains.yml"
    records = records_of(rec(1, "Alert", "2026-11-01T00:00:00+00:00"))
    assert save_alert_chains(path, records) is True
    assert path.read_text(encoding="utf-8") == format_alert_chains(records)
    mtime = path.stat().st_mtime_ns
    assert save_alert_chains(path, records) is False
    assert path.stat().st_mtime_ns == mtime


def test_save_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "alert-chains.yml"
    save_alert_chains(path, {})
    original = path.read_bytes()

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("cap_poller.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        save_alert_chains(path, records_of(rec(1, "Alert", "2026-11-01T00:00:00+00:00")))
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@responses.activate
def test_poll_regenerates_chains_from_the_csv(make_feed, alert_xml, cancel_xml, warnings_dir, tmp_path):
    csv_path, chains_path = tmp_path / "alerts.csv", tmp_path / "alert-chains.yml"
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514"]))
    responses.add(responses.GET, message_url("132514"), body=cancel_xml)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    result = poll_once(warnings_dir, csv_path=csv_path, chains_path=chains_path)

    assert result.chains_changed is True
    assert chains_path.read_text(encoding="utf-8") == format_alert_chains(read_alert_records(csv_path))
    assert poll_once(warnings_dir, csv_path=csv_path, chains_path=chains_path).chains_changed is False


@responses.activate
def test_poll_writes_chains_to_the_default_path(make_feed, alert_xml, warnings_dir, tmp_path):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    poll_once(warnings_dir)
    assert (tmp_path / "alert-chains.yml").exists()


def test_backfill_also_rebuilds_chains(alert_xml, cancel_xml, warnings_dir, tmp_path):
    save_message(warnings_dir, "132508", alert_xml)
    save_message(warnings_dir, "132514", cancel_xml)
    csv_path, chains_path = tmp_path / "alerts.csv", tmp_path / "alert-chains.yml"
    assert backfill_alert_records(warnings_dir, csv_path, chains_path) == 2
    assert chains_path.read_text(encoding="utf-8") == format_alert_chains(read_alert_records(csv_path))


@responses.activate
def test_cli_chains_file_option_is_used_for_poll_and_backfill(make_feed, alert_xml, warnings_dir, tmp_path):
    polled, backfilled = tmp_path / "polled.yml", tmp_path / "backfilled.yml"
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    assert main(["--warnings-dir", str(warnings_dir), "--chains-file", str(polled)]) == 0
    assert main(["--backfill-summary-data", "--warnings-dir", str(warnings_dir), "--chains-file", str(backfilled)]) == 0
    assert polled.read_bytes() == backfilled.read_bytes()
    assert yaml.safe_load(polled.read_text())["chains"][0]["alert_id"] == 132508


def test_summary_and_commit_message_report_chain_changes():
    assert "Alert chains updated: yes" in format_summary(PollResult(chains_changed=True))
    assert "Alert chains updated: no" in format_summary(PollResult())
    assert format_commit_message(PollResult(chains_changed=True)) == "data: Update alert chains"
    assert format_commit_message(PollResult(csv_changed=True, chains_changed=True)) == "data: Update CAP alert records"


def test_committed_chains_match_the_committed_csv():
    records = read_alert_records(REPO / "alerts.csv")
    assert (REPO / "alert-chains.yml").read_text(encoding="utf-8") == format_alert_chains(records)


def test_committed_chains_match_the_archived_xml():
    records = {p.stem: alert_record(p.stem, p.read_bytes()) for p in (REPO / "warnings").glob("*.xml")}
    assert (REPO / "alert-chains.yml").read_text(encoding="utf-8") == format_alert_chains(records)


def test_poll_and_backfill_both_name_the_csv_they_read(alert_xml, warnings_dir, tmp_path):
    save_message(warnings_dir, "132508", alert_xml)
    csv_path, chains_path = tmp_path / "other.csv", tmp_path / "alert-chains.yml"
    backfill_alert_records(warnings_dir, csv_path, chains_path)
    assert yaml.safe_load(chains_path.read_text())["source"] == "other.csv"
