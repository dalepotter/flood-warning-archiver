from cap_poller import PollResult, format_commit_message, format_summary, write_github_outputs


def test_outputs_are_written_when_running_in_actions(tmp_path):
    out = tmp_path / "output"
    result = PollResult(new_ids=["132562", "132570"])

    write_github_outputs(result, {"GITHUB_OUTPUT": str(out)})

    assert out.read_text() == (
        "new_count=2\nnew_ids=132562, 132570\nupdated_count=0\nupdated_ids=\nfeed_changed=false\n"
        "commit_message<<EOF\n"
        "data: Archive 2 new CAP messages\n\nNew: 132562, 132570\n"
        "EOF\n"
    )


def test_updated_messages_are_reported_in_outputs_and_summary(tmp_path):
    out, summary = tmp_path / "output", tmp_path / "summary.md"
    write_github_outputs(
        PollResult(updated_ids=["132562"]),
        {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)},
    )

    assert "updated_count=1\nupdated_ids=132562\n" in out.read_text()
    assert "Existing messages updated: 1" in summary.read_text()
    assert "- 132562" in summary.read_text()


def test_commit_message_is_concise_for_one_new_message():
    assert format_commit_message(PollResult(new_ids=["133652"], feed_changed=True)) == (
        "data: Archive CAP message 133652"
    )


def test_commit_message_lists_multiple_new_and_updated_ids():
    result = PollResult(new_ids=["1", "2"], updated_ids=["3"], feed_changed=True)
    assert format_commit_message(result) == (
        "data: Archive 2 new and 1 updated CAP messages\n\nNew: 1, 2\nUpdated: 3"
    )


def test_commit_message_identifies_feed_only_change():
    assert format_commit_message(PollResult(feed_changed=True)) == "data: Update CAP feed index"


def test_feed_changed_output_and_summary_reflect_the_result(tmp_path):
    out, summary = tmp_path / "output", tmp_path / "summary.md"
    result = PollResult(feed_changed=True)

    write_github_outputs(result, {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)})

    assert "feed_changed=true\n" in out.read_text()
    assert "Feed updated: yes" in summary.read_text()


def test_summary_is_written_when_running_in_actions(tmp_path):
    summary = tmp_path / "summary.md"
    write_github_outputs(PollResult(new_ids=["1"]), {"GITHUB_STEP_SUMMARY": str(summary)})
    assert "New messages archived: 1" in summary.read_text()


def test_nothing_is_written_outside_actions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_github_outputs(PollResult(new_ids=["1"]), {})
    assert list(tmp_path.iterdir()) == []


def test_summary_lists_failures_with_reasons():
    result = PollResult(failed={"132514": "HTTP 500"})
    text = format_summary(result)
    assert "Failed: 1" in text
    assert "- 132514: HTTP 500" in text


def test_summary_lists_new_message_ids():
    text = format_summary(PollResult(new_ids=["132562", "132570"]))
    assert "- 132562" in text
    assert "- 132570" in text


def test_summary_reports_unavailable_references_without_calling_them_failures():
    text = format_summary(PollResult(unavailable_references={"132508": "HTTP 404"}))
    assert "Referenced but unavailable: 1" in text
    assert "Failed: 0" in text
