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


@responses.activate
def test_fetch_retries_a_503_then_succeeds(sleeps):
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body=b"ok")
    assert fetch(URL) == b"ok"
    assert sleeps == [2.0]


@responses.activate
def test_fetch_backs_off_exponentially_and_gives_up(sleeps):
    responses.add(responses.GET, URL, status=503)
    with pytest.raises(FetchError, match="3 attempt"):
        fetch(URL)
    assert len(responses.calls) == 3
    assert sleeps == [2.0, 4.0]


@responses.activate
def test_fetch_does_not_retry_a_404(sleeps):
    responses.add(responses.GET, URL, status=404)
    with pytest.raises(FetchError):
        fetch(URL)
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_fetch_retries_a_connection_error_then_succeeds():
    responses.add(responses.GET, URL, body=requests.ConnectionError("reset"))
    responses.add(responses.GET, URL, body=b"ok")
    assert fetch(URL) == b"ok"


def test_fetch_wraps_non_retryable_request_errors_without_retrying(sleeps):
    with pytest.raises(FetchError):
        fetch("not-a-url")  # requests raises MissingSchema, which is a RequestException
    assert sleeps == []
