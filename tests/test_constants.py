from pathlib import Path

import cap_poller


def test_feed_url_is_the_ea_cap_atom_feed():
    assert cap_poller.FEED_URL == "https://cap-xml.prd.defra.cloud/v2/messages.atom"


def test_default_archive_folder_is_warnings():
    assert cap_poller.WARNINGS_DIR == Path("warnings")
