from __future__ import annotations

from greeting import fetch_greeting


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


def test_fetch_greeting_formats_zenquotes() -> None:
    def get(url, timeout=None):
        assert "zenquotes.io" in url
        return FakeResponse([{"q": "Do the work.", "a": "Someone"}])

    text = fetch_greeting(http_get=get)
    assert text.startswith("Good morning.")
    assert '"Do the work." — Someone' in text


def test_fetch_greeting_falls_back_when_http_fails() -> None:
    def get(url, timeout=None):
        raise TimeoutError("offline")

    text = fetch_greeting(http_get=get)
    assert text.startswith("Good morning.")
