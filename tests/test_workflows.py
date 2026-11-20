import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "poll.yml"


def load_workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML follows YAML 1.1, which reads the bare key `on` as the boolean True.
    triggers = data.get("on", data.get(True))
    return data, triggers


def test_poll_workflow_runs_hourly_and_can_be_run_manually():
    _, triggers = load_workflow()
    assert re.fullmatch(r"\d{1,2} \* \* \* \*", triggers["schedule"][0]["cron"])
    assert "workflow_dispatch" in triggers


def test_poll_workflow_has_write_access_to_contents_only():
    data, _ = load_workflow()
    assert data["permissions"] == {"contents": "write"}


def test_poll_workflow_never_overlaps_runs():
    data, _ = load_workflow()
    assert data["concurrency"]["cancel-in-progress"] is False


def test_poll_workflow_only_commits_archived_messages_and_the_feed():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    commit = next(s for s in steps if s.get("uses", "").startswith("stefanzweifel/git-auto-commit-action@"))
    assert commit["with"]["file_pattern"].split() == ["warnings/*.xml", "feed/messages.atom"]


def test_poll_workflow_checks_out_the_branch_head_not_the_triggering_sha():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def test_poll_workflow_still_fails_the_run_when_polling_fails():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    poll = next(s for s in steps if s.get("id") == "poll")
    assert poll["continue-on-error"] is True
    assert any(s.get("if") == "steps.poll.outcome == 'failure'" for s in steps)


def test_commit_message_names_the_archived_messages():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    commit = next(s for s in steps if s.get("uses", "").startswith("stefanzweifel/git-auto-commit-action@"))
    message = commit["with"]["commit_message"]
    assert "steps.poll.outputs.new_count" in message
    assert "steps.poll.outputs.new_ids" in message
    assert "steps.poll.outputs.updated_count" in message
    assert "steps.poll.outputs.updated_ids" in message
    assert "steps.poll.outputs.feed_changed" in message
