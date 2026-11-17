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
