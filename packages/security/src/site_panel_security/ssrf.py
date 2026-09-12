from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

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


@dataclass
class SSRFGuard:
    """DNS resolve + private IP block + socket-level pin for outbound fetches."""

    timeout: float = 10.0
    max_response_bytes: int = 2_000_000
    allow_redirects: bool = False

    def resolve_safe(self, host: str) -> str:
        infos = socket.getaddrinfo(host, None)
        if not infos:
            raise SSRFBlockedError(f"Cannot resolve host: {host}")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if _is_blocked(ip):
                raise SSRFBlockedError(f"Blocked private/loopback IP for {host}: {ip}")
        # Pin first public IP
        return str(ipaddress.ip_address(infos[0][4][0]))

    def validate_url(self, url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise SSRFBlockedError("Only http/https allowed")
        if not parsed.hostname:
            raise SSRFBlockedError("Missing hostname")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        pinned_ip = self.resolve_safe(parsed.hostname)
        return pinned_ip, parsed.hostname, port

    async def fetch(self, url: str) -> httpx.Response:
        pinned_ip, hostname, port = self.validate_url(url)
        parsed = urlparse(url)
        # Connect to pinned IP, send Host header for virtual hosts
        transport_url = f"{parsed.scheme}://{pinned_ip}:{port}{parsed.path or '/'}"
        if parsed.query:
            transport_url += f"?{parsed.query}"

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=self.allow_redirects,
            max_redirects=0,
        ) as client:
            response = await client.get(
                transport_url,
                headers={"Host": hostname, "User-Agent": "SitePanelBot/0.1 (+internal)"},
            )
            content = response.content
            if len(content) > self.max_response_bytes:
                raise SSRFBlockedError("Response too large")
            return response
