from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class SSRFBlockedError(Exception):
    """Raised when a URL resolves to a forbidden address (CWE-918)."""


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Map IPv4-mapped IPv6
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _BLOCKED_NETWORKS)


@dataclass(frozen=True)
class PinnedURL:
    transport_url: str
    host_header: str
    sni_hostname: str


@dataclass
class SSRFGuard:
    """DNS resolve + public-IP policy + socket-level pin for outbound fetches."""

    timeout: float = 10.0
    max_response_bytes: int = 2_000_000
    allow_redirects: bool = False
    require_global_ips: bool = False
    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_ports: tuple[int, ...] | None = None

    def resolve_safe(self, host: str) -> str:
        infos = socket.getaddrinfo(host, None)
        if not infos:
            raise SSRFBlockedError(f"Cannot resolve host: {host}")
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        for ip in addresses:
            if _is_blocked(ip) or (self.require_global_ips and not ip.is_global):
                raise SSRFBlockedError(f"Blocked non-public IP for {host}: {ip}")
        return str(addresses[0])

    def validate_url(self, url: str) -> tuple[str, str, int]:
        parsed = urlsplit(url)
        if parsed.scheme not in self.allowed_schemes:
            raise SSRFBlockedError("URL scheme is not allowed")
        if not parsed.hostname:
            raise SSRFBlockedError("Missing hostname")
        if parsed.username or parsed.password:
            raise SSRFBlockedError("URL credentials are not allowed")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise SSRFBlockedError("Invalid port") from exc
        if self.allowed_ports is not None and port not in self.allowed_ports:
            raise SSRFBlockedError("URL port is not allowed")
        pinned_ip = self.resolve_safe(parsed.hostname)
        return pinned_ip, parsed.hostname, port

    def pin_url(self, url: str) -> PinnedURL:
        pinned_ip, hostname, port = self.validate_url(url)
        parsed = urlsplit(url)
        default_port = 443 if parsed.scheme == "https" else 80
        transport_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        host_header = hostname if port == default_port else f"{hostname}:{port}"
        transport_url = f"{parsed.scheme}://{transport_host}:{port}{parsed.path or '/'}"
        if parsed.query:
            transport_url += f"?{parsed.query}"
        return PinnedURL(
            transport_url=transport_url,
            host_header=host_header,
            sni_hostname=hostname,
        )

    def _request(self, client: httpx.AsyncClient, pinned: PinnedURL) -> httpx.Request:
        return client.build_request(
            "GET",
            pinned.transport_url,
            headers={"Host": pinned.host_header, "User-Agent": "SitePanelBot/0.1 (+internal)"},
            extensions={"sni_hostname": pinned.sni_hostname},
        )

    async def fetch_text(
        self,
        url: str,
        *,
        accepted_content_types: Iterable[str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Fetch a bounded response without buffering beyond the configured limit."""
        pinned = self.pin_url(url)
        accepted = tuple(item.lower() for item in accepted_content_types or ())
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=self.allow_redirects,
            max_redirects=0,
        ) as client:
            request = self._request(client, pinned)
            response = await client.send(request, stream=True)
            try:
                content_type = response.headers.get("content-type", "").lower()
                if accepted and not any(content_type.startswith(item) for item in accepted):
                    raise SSRFBlockedError("Response content type is not allowed")
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self.max_response_bytes:
                    raise SSRFBlockedError("Response too large")
                parts: list[bytes] = []
                received = 0
                async for part in response.aiter_bytes():
                    received += len(part)
                    if received > self.max_response_bytes:
                        raise SSRFBlockedError("Response too large")
                    parts.append(part)
                return response.status_code, dict(response.headers), b"".join(parts)
            finally:
                await response.aclose()

    async def fetch(self, url: str) -> httpx.Response:
        pinned = self.pin_url(url)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=self.allow_redirects,
            max_redirects=0,
        ) as client:
            response = await client.send(self._request(client, pinned))
            content = response.content
            if len(content) > self.max_response_bytes:
                raise SSRFBlockedError("Response too large")
            return response
