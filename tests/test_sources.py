import httpx
import pytest

from app.sources.api import ApiSource
from app.sources.base import FatalSourceError, Source


class Flaky(Source):
    name = "flaky"
    RETRY_DELAYS = (0,)

    def __init__(self, failures, *a, **kw):
        super().__init__(*a, **kw)
        self.failures = failures
        self.calls = 0

    def run(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("jaringan putus")
        self.connected()
        self.emit("x", "member", "A")


def wait(src):
    src._thread.join(timeout=5)
    assert not src._thread.is_alive()


def test_source_reconnects_after_errors():
    got, notes = [], []
    src = Flaky(3, lambda *a: got.append(a), dict, notify=notes.append)
    src.start()
    wait(src)
    assert src.calls == 4 and len(got) == 1
    assert src.error == "" and src.retries == 0
    assert any("terputus" in n for n in notes) and any("kembali" in n for n in notes)


def test_fatal_error_stops_without_retry():
    class Bad(Source):
        def run(self):
            self.n = getattr(self, "n", 0) + 1
            raise FatalSourceError("URL live belum diisi")

    notes = []
    src = Bad(lambda *a: None, dict, notify=notes.append)
    src.start()
    wait(src)
    assert src.n == 1 and "URL" in src.error and notes


def test_stop_interrupts_retry_wait():
    src = Flaky(99, lambda *a: None, dict)
    src.RETRY_DELAYS = (60,)
    src.start()
    src.stop()
    wait(src)


@pytest.mark.parametrize("reason", ["quotaExceeded", "liveChatEnded", "forbidden"])
def test_api_403_is_fatal(reason):
    r = httpx.Response(403, json={"error": {"errors": [{"reason": reason}]}}, request=httpx.Request("GET", "http://x"))
    with pytest.raises(FatalSourceError):
        ApiSource._check(r)


def test_api_5xx_is_retryable():
    r = httpx.Response(503, request=httpx.Request("GET", "http://x"))
    with pytest.raises(httpx.HTTPStatusError):
        ApiSource._check(r)
