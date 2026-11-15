import asyncio
import json

import httpx
import pytest

from fakeweb import wayback_api
from link_rot_checker.wayback import WaybackClient, parse_availability


def test_parse_documented_example():
    # The example answer from https://archive.org/help/wayback_api.php
    data = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": "http://web.archive.org/web/20060101064348/http://www.example.com:80/",
                "timestamp": "20060101064348",
                "status": "200",
            }
        }
    }
    snap = parse_availability(data)
    assert snap.url == "https://web.archive.org/web/20060101064348/http://www.example.com:80/"
    assert snap.timestamp == "20060101064348"


@pytest.mark.parametrize(
    "data",
    [
        {"archived_snapshots": {}},
        {"archived_snapshots": {"closest": {"available": False, "url": "http://web.archive.org/x"}}},
        {"archived_snapshots": {"closest": {"available": True, "url": "http://web.archive.org/x", "status": "404"}}},
        {"archived_snapshots": {"closest": {"available": True, "url": "javascript:alert(1)"}}},
        [],
        None,
    ],
)
def test_parse_unusable(data):
    assert parse_availability(data) is None


def run(client_kwargs, urls, transport):
    async def go():
        async with httpx.AsyncClient(transport=transport) as http:
            client = WaybackClient(http, rate=0, **client_kwargs)
            out = [await client.lookup(u) for u in urls]
            client.save_cache()
            return out, client

    return asyncio.run(go())


def test_lookup_cache_and_timestamp(tmp_path):
    seen = []

    def handle(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "archived_snapshots": {
                    "closest": {
                        "available": True,
                        "url": "http://web.archive.org/web/2019/https://a.test/",
                        "timestamp": "2019",
                        "status": "200",
                    }
                }
            },
        )

    cache = tmp_path / "wb.json"
    out, client = run(
        {"timestamp": "2019", "cache_file": cache}, ["https://a.test/", "https://a.test/"], httpx.MockTransport(handle)
    )
    assert out[0].url.startswith("https://web.archive.org/") and out[0] == out[1]
    assert seen == [{"url": "https://a.test/", "timestamp": "2019"}]
    assert client.lookups == 1
    assert "2019|https://a.test/" in json.loads(cache.read_text())
    # A second client reads the cache and makes no request.
    out2, client2 = run({"timestamp": "2019", "cache_file": cache}, ["https://a.test/"], httpx.MockTransport(handle))
    assert client2.lookups == 0 and out2[0].url == out[0].url


def test_lookup_errors_are_not_cached():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    out, client = run({"retries": 0}, ["https://a.test/", "https://a.test/"], httpx.MockTransport(handle))
    assert out == [None, None] and calls == 2 and client.errors


def test_lookup_none_found():
    out, _ = run({}, ["https://nothing.test/"], wayback_api({}))
    assert out == [None]


def test_invalid_timestamp():
    with pytest.raises(ValueError):
        WaybackClient(httpx.AsyncClient(), timestamp="2020-01-01")
