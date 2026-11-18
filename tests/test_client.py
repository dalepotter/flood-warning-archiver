import pytest
import requests
import responses

from cap_poller import FetchError, fetch

URL = "https://example.test/thing"


@responses.activate
def test_fetch_returns_body_bytes():
    responses.add(responses.GET, URL, body=b"<xml/>")
    assert fetch(URL) == b"<xml/>"


@responses.activate
def test_fetch_sends_user_agent():
    responses.add(responses.GET, URL, body=b"")
    fetch(URL)
    assert "flood-warning-archiver" in responses.calls[0].request.headers["User-Agent"]


@responses.activate
@pytest.mark.parametrize("status", [403, 404, 500])
def test_fetch_raises_fetch_error_on_http_error(status):
    responses.add(responses.GET, URL, status=status)
    with pytest.raises(FetchError):
        fetch(URL)


@responses.activate
@pytest.mark.parametrize("error", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_fetch_raises_fetch_error_on_network_error(error):
    responses.add(responses.GET, URL, body=error)
    with pytest.raises(FetchError):
        fetch(URL)
