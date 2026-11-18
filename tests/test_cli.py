import logging
import runpy
import sys
from pathlib import Path

import pytest
import responses

from cap_poller import FEED_URL, main, message_url


@responses.activate
def test_main_archives_new_messages_and_returns_zero(make_feed, alert_xml, warnings_dir, caplog):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    with caplog.at_level(logging.INFO):
        code = main(["--warnings-dir", str(warnings_dir)])

    assert code == 0
    assert (warnings_dir / "132508.xml").read_bytes() == alert_xml
    assert "Archived 1 new message(s)" in caplog.text


@responses.activate
def test_main_returns_zero_when_nothing_is_new(make_feed, warnings_dir):
    responses.add(responses.GET, FEED_URL, body=make_feed([]))
    assert main(["--warnings-dir", str(warnings_dir)]) == 0


@responses.activate
def test_main_returns_one_when_the_feed_is_unavailable(warnings_dir):
    responses.add(responses.GET, FEED_URL, status=503)
    assert main(["--warnings-dir", str(warnings_dir)]) == 1


@responses.activate
def test_main_returns_one_when_the_feed_is_not_atom(warnings_dir):
    responses.add(responses.GET, FEED_URL, body=b"<html>Forbidden</html>")
    assert main(["--warnings-dir", str(warnings_dir)]) == 1


@responses.activate
def test_main_returns_one_on_partial_failure_but_still_archives_the_rest(
    make_feed, alert_xml, warnings_dir
):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132514", "132508"]))
    responses.add(responses.GET, message_url("132514"), status=404)
    responses.add(responses.GET, message_url("132508"), body=alert_xml)

    assert main(["--warnings-dir", str(warnings_dir)]) == 1
    assert (warnings_dir / "132508.xml").exists()


@responses.activate
def test_running_the_script_directly_exits_with_main_return_code(make_feed, alert_xml, warnings_dir, monkeypatch):
    responses.add(responses.GET, FEED_URL, body=make_feed(["132508"]))
    responses.add(responses.GET, message_url("132508"), body=alert_xml)
    script = Path(__file__).parent.parent / "cap_poller.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--warnings-dir", str(warnings_dir)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script), run_name="__main__")

    assert excinfo.value.code == 0
    assert (warnings_dir / "132508.xml").exists()
