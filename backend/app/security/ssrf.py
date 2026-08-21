"""SSRF-hardened outbound HTTP client.

This is the *only* sanctioned egress path for providers. The guard closes the four
failure modes that plain allow/deny lists leave open:

1. literal private / loopback / link-local / reserved targets      -> IP classification
2. hostnames that resolve to private space                          -> post-resolution check
3. redirects that hop from a public host into private space         -> manual hop validation
4. DNS rebinding (public at check time, private at connect time)    -> the resolved IP is
   pinned and the connection is made to that IP with an SNI/Host override, so no second
   name lookup ever happens
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.config import Settings, get_settings

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Hosts that must never be reachable even if they resolve to a public-looking address.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

#: Cloud instance-metadata addresses, blocked explicitly as well as by range.
BLOCKED_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})


class SSRFBlocked(ValueError):
    """Raised when a URL is refused by the egress guard."""


class ResponseTooLarge(SSRFBlocked):
    pass


@dataclass(frozen=True)
class ResolvedTarget:
    host: str
    ip: str
    port: int
    family: int


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if str(ip) in BLOCKED_ADDRESSES:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        # Carrier-grade NAT and the "this network" block are not routable targets.
        if ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("0.0.0.0/8"):
            return False
    else:
        if ip.ipv4_mapped is not None:
            return _is_public_ip(ip.ipv4_mapped)
        if ip.is_site_local:
            return False
    return True


def validate_url(url: str, settings: Settings | None = None) -> httpx.URL:
    """Structural validation: scheme, credentials, host shape, port."""
    settings = settings or get_settings()
    try:
        parsed = httpx.URL(url)
    except Exception as exc:  # pragma: no cover - httpx raises several types
        raise SSRFBlocked(f"unparseable URL: {exc}") from exc

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked(f"scheme not allowed: {parsed.scheme or '(none)'}")
    if parsed.userinfo:
        raise SSRFBlocked("credentials in URL are not allowed")
    host = parsed.host
    if not host:
        raise SSRFBlocked("URL has no host")
    if host.lower() in BLOCKED_HOSTNAMES:
        raise SSRFBlocked(f"host not allowed: {host}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not settings.allow_private_networks and port not in settings.allowed_egress_ports:
        raise SSRFBlocked(f"port not allowed: {port}")
    return parsed


def _resolve(host: str, port: int) -> list[tuple[int, str]]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SSRFBlocked(f"cannot resolve host {host}: {exc}") from exc
    out: list[tuple[int, str]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        addr = str(sockaddr[0])
        if (family, addr) not in out:
            out.append((family, addr))
    if not out:
        raise SSRFBlocked(f"host {host} resolved to nothing")
    return out


def resolve_and_validate(url: str, settings: Settings | None = None) -> ResolvedTarget:
    """Validate, resolve, and pin. Every resolved address must be public."""
    settings = settings or get_settings()
    parsed = validate_url(url, settings)
    host = parsed.host
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if not settings.allow_private_networks and not _is_public_ip(literal):
            raise SSRFBlocked(f"address not allowed: {host}")
        literal_family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return ResolvedTarget(host=host, ip=str(literal), port=port, family=literal_family)

    candidates = _resolve(host, port)
    if not settings.allow_private_networks:
        # Every answer must be public: one private answer means the name is untrustworthy.
        for _family, address in candidates:
            if not _is_public_ip(ipaddress.ip_address(address)):
                raise SSRFBlocked(f"host {host} resolves to a non-public address ({address})")
    resolved_family, resolved_address = candidates[0]
    return ResolvedTarget(
        host=host, ip=resolved_address, port=port, family=socket.AddressFamily(resolved_family)
    )


class SafeAsyncClient:
    """Async HTTP client with the guard applied to every request and every redirect hop."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float | None = None,
        max_bytes: int | None = None,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout = timeout if timeout is not None else self.settings.http_timeout_seconds
        self.max_bytes = max_bytes if max_bytes is not None else self.settings.http_max_bytes
        self._pin_connections = transport is None
        base_headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        base_headers.update(headers or {})
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            headers=base_headers,
            transport=transport,
            verify=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _pinned_request(self, method: str, url: httpx.URL, **kwargs: Any) -> httpx.Request:
        """Build a request aimed at the pinned IP, preserving Host and TLS SNI."""
        target = resolve_and_validate(str(url), self.settings)
        if not self._pin_connections:
            return self._client.build_request(method, url, **kwargs)

        ip_host = f"[{target.ip}]" if ":" in target.ip else target.ip
        pinned = url.copy_with(host=ip_host, port=target.port)
        request = self._client.build_request(method, pinned, **kwargs)
        request.headers["Host"] = url.netloc.decode("ascii")
        # httpx passes this through to the TLS layer, so certificate validation still
        # happens against the real hostname rather than the raw IP.
        request.extensions = {**request.extensions, "sni_hostname": target.host}
        return request

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        current = validate_url(url, self.settings)
        hops = 0
        while True:
            request = self._pinned_request(method, current, **kwargs)
            response = await self._client.send(request, stream=True)
            try:
                if response.is_redirect and response.has_redirect_location:
                    hops += 1
                    if hops > self.settings.http_max_redirects:
                        raise SSRFBlocked("too many redirects")
                    location = response.headers["location"]
                    nxt = current.join(location)
                    # Re-validate the hop before following it (threat model B3).
                    current = validate_url(str(nxt), self.settings)
                    if response.status_code in (301, 302, 303) and method.upper() not in (
                        "GET",
                        "HEAD",
                    ):
                        method = "GET"
                        kwargs.pop("content", None)
                        kwargs.pop("data", None)
                        kwargs.pop("json", None)
                    await response.aclose()
                    continue
                await self._read_capped(response)
                return response
            except BaseException:
                await response.aclose()
                raise

    async def _read_capped(self, response: httpx.Response) -> None:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise ResponseTooLarge(f"response exceeded {self.max_bytes} bytes")
            chunks.append(chunk)
        # Re-seat the fully-read body so callers can use .text/.json() normally.
        response._content = b"".join(chunks)
        response.is_stream_consumed = True

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("HEAD", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
