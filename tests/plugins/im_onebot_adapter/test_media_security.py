import socket
from types import SimpleNamespace

import pytest

from kirara_ai.plugins.im_onebot_adapter.utils import media as media_security


class FakeResolver:
    def __init__(self, addresses):
        self.addresses = addresses

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": family,
                "proto": 0,
                "flags": 0,
            }
            for address in self.addresses
        ]

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_public_resolver_rejects_private_dns_answers():
    resolver = media_security.PublicResolver(FakeResolver(["203.0.113.10", "10.0.0.8"]))

    with pytest.raises(ValueError, match="非公网地址"):
        await resolver.resolve("cdn.example.com", 443)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/file",
        "http://[::1]/file",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/file",
        "ftp://example.com/file",
        "https://user:secret@example.com/file",
        "../local-file",
    ],
)
def test_validate_public_media_url_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        media_security.validate_public_media_url(url)


def test_decode_inline_media_enforces_base64_and_size_limit():
    assert media_security.decode_inline_media(
        "data:image/png;base64,c2FmZQ==", max_bytes=4
    ) == b"safe"

    with pytest.raises(ValueError, match="大小上限"):
        media_security.decode_inline_media(
            "data:image/png;base64,c2FmZQ==", max_bytes=3
        )
    with pytest.raises(ValueError, match="Base64"):
        media_security.decode_inline_media(
            "data:image/png,not-base64", max_bytes=100
        )


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status, *, headers=None, chunks=()):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakeSession:
    responses = []
    init_kwargs = []
    requested = []

    def __init__(self, **kwargs):
        self.init_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def get(self, url, **kwargs):
        self.requested.append((url, kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def reset_fake_session():
    FakeSession.responses = []
    FakeSession.init_kwargs = []
    FakeSession.requested = []


@pytest.mark.asyncio
async def test_download_disables_environment_proxy_and_streams_bounded_body(monkeypatch):
    FakeSession.responses = [
        FakeResponse(200, headers={"Content-Length": "4"}, chunks=[b"sa", b"fe"])
    ]
    monkeypatch.setattr(media_security.aiohttp, "ClientSession", FakeSession)

    result = await media_security.download_public_media(
        "https://cdn.example.com/file.png", max_bytes=4, timeout_seconds=5
    )

    assert result == b"safe"
    assert FakeSession.init_kwargs[0]["trust_env"] is False
    assert FakeSession.requested == [
        ("https://cdn.example.com/file.png", {"allow_redirects": False})
    ]


@pytest.mark.asyncio
async def test_download_revalidates_redirect_target(monkeypatch):
    FakeSession.responses = [
        FakeResponse(302, headers={"Location": "http://127.0.0.1/private"})
    ]
    monkeypatch.setattr(media_security.aiohttp, "ClientSession", FakeSession)

    with pytest.raises(ValueError, match="非公网地址"):
        await media_security.download_public_media(
            "https://cdn.example.com/file.png", max_bytes=1024, timeout_seconds=5
        )

    assert len(FakeSession.requested) == 1


@pytest.mark.asyncio
async def test_download_rejects_stream_larger_than_limit(monkeypatch):
    FakeSession.responses = [FakeResponse(200, chunks=[b"1234", b"5"])]
    monkeypatch.setattr(media_security.aiohttp, "ClientSession", FakeSession)

    with pytest.raises(ValueError, match="大小上限"):
        await media_security.download_public_media(
            "https://cdn.example.com/file.png", max_bytes=4, timeout_seconds=5
        )
