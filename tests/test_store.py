import pytest

from cap_poller import is_stored, message_path, save_message


def test_message_path_uses_message_id_as_filename(warnings_dir):
    assert message_path(warnings_dir, "132514") == warnings_dir / "132514.xml"


@pytest.mark.parametrize("bad_id", ["", "../x", "12a", "1/2", "١٢٣"])
def test_message_path_rejects_ids_that_are_not_ascii_digits(warnings_dir, bad_id):
    with pytest.raises(ValueError):
        message_path(warnings_dir, bad_id)


def test_is_stored_is_false_for_unknown_message(warnings_dir):
    assert is_stored(warnings_dir, "1") is False


def test_save_message_creates_folder_and_returns_true(warnings_dir):
    assert save_message(warnings_dir, "1", b"<alert/>") is True
    assert is_stored(warnings_dir, "1")


def test_save_message_preserves_bytes_exactly(warnings_dir):
    raw = b"\xef\xbb\xbf<?xml version='1.0'?>\r\n<alert>caf\xc3\xa9</alert>\r\n"  # BOM, CRLF, UTF-8
    save_message(warnings_dir, "1", raw)
    assert (warnings_dir / "1.xml").read_bytes() == raw


def test_save_message_never_overwrites(warnings_dir):
    save_message(warnings_dir, "1", b"original")
    assert save_message(warnings_dir, "1", b"changed") is False
    assert (warnings_dir / "1.xml").read_bytes() == b"original"


def test_save_message_leaves_no_temporary_files(warnings_dir):
    save_message(warnings_dir, "1", b"<alert/>")
    assert [p.name for p in warnings_dir.iterdir()] == ["1.xml"]


def test_save_message_cleans_up_when_writing_fails(warnings_dir, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("cap_poller.os.replace", boom)
    with pytest.raises(OSError):
        save_message(warnings_dir, "1", b"<alert/>")
    assert list(warnings_dir.iterdir()) == []
