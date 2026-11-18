from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> bytes:
    """Read a fixture file as raw bytes."""
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def warnings_dir(tmp_path):
    """A not-yet-created archive folder inside a temporary directory."""
    return tmp_path / "warnings"


@pytest.fixture
def feed_xml() -> bytes:
    return read_fixture("messages.atom")


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Never really sleep in tests; expose the requested delays instead."""
    delays = []
    monkeypatch.setattr("cap_poller.time.sleep", delays.append)
    return delays


@pytest.fixture
def alert_xml() -> bytes:
    return read_fixture("message-132508.xml")


@pytest.fixture
def cancel_xml() -> bytes:
    return read_fixture("message-132514.xml")


@pytest.fixture
def make_feed():
    """Build an ATOM index containing the given message IDs (newest first)."""

    def _make(ids):
        entries = "".join(
            f'<entry><title type="html"><![CDATA[AREA{i}]]></title>'
            f"<id>https://cap-xml.prd.defra.cloud/v2/message/{i}</id>"
            f"<updated>2026-11-02T09:35:32.000Z</updated></entry>"
            for i in ids
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Flood warnings for England</title>{entries}</feed>'
        ).encode()

    return _make
