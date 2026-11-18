from cap_poller import PollResult, format_summary, write_github_outputs


def test_outputs_are_written_when_running_in_actions(tmp_path):
    out = tmp_path / "output"
    result = PollResult(new_ids=["132562", "132570"])

    write_github_outputs(result, {"GITHUB_OUTPUT": str(out)})

    assert out.read_text() == "new_count=2\nnew_ids=132562, 132570\nfeed_changed=false\n"


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
