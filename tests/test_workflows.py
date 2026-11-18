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


def test_poll_workflow_only_commits_archived_messages():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    commit = next(s for s in steps if s.get("uses", "").startswith("stefanzweifel/git-auto-commit-action@"))
    assert commit["with"]["file_pattern"] == "warnings/*.xml"


def test_poll_workflow_still_fails_the_run_when_polling_fails():
    data, _ = load_workflow()
    steps = data["jobs"]["poll"]["steps"]
    poll = next(s for s in steps if s.get("id") == "poll")
    assert poll["continue-on-error"] is True
    assert any(s.get("if") == "steps.poll.outcome == 'failure'" for s in steps)
