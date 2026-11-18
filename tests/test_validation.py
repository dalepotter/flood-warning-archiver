import pytest

from cap_poller import InvalidMessageError, referenced_message_ids, validate_cap_message


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


def test_referenced_message_ids_reads_the_message_number_from_each_reference(cancel_xml):
    assert referenced_message_ids(cancel_xml) == ["132508"]


def test_referenced_message_ids_is_empty_without_references(alert_xml):
    assert referenced_message_ids(alert_xml) == []


def test_referenced_message_ids_ignores_malformed_references():
    xml = (
        b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
        b"<references>not-a-triple a,b.x,c ../../etc,x.99,2026</references></alert>"
    )
    assert referenced_message_ids(xml) == ["99"]
