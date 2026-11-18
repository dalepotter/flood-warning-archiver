import pytest

from cap_poller import InvalidMessageError, validate_cap_message


def test_alert_is_valid(alert_xml):
    validate_cap_message(alert_xml)  # does not raise


def test_cancel_is_valid(cancel_xml):
    validate_cap_message(cancel_xml)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html><body>Forbidden</body></html>",
        b"<alert>no namespace</alert>",
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.1"/>',
        b"<alert xmlns=",
    ],
)
def test_non_cap_content_is_rejected(content):
    with pytest.raises(InvalidMessageError):
        validate_cap_message(content)


def test_entity_declarations_are_rejected():
    content = (
        b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]>'
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">&x;</alert>'
    )
    with pytest.raises(InvalidMessageError):
        validate_cap_message(content)
