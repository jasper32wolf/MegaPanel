"""Domain DNS/TLS health probes (TZ 17.11)."""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime
from typing import Any


def resolve_dns(hostname: str) -> dict[str, Any]:
    host = hostname.strip().lower().rstrip(".")
    out: dict[str, Any] = {"hostname": host, "a": [], "aaaa": [], "ok": False, "error": None}
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        for family, _type, _proto, _canon, sockaddr in infos:
            ip = sockaddr[0]
            if family == socket.AF_INET and ip not in out["a"]:
                out["a"].append(ip)
            elif family == socket.AF_INET6 and ip not in out["aaaa"]:
                out["aaaa"].append(ip)
        out["ok"] = bool(out["a"] or out["aaaa"])
        out["status"] = "ok" if out["ok"] else "nxdomain"
    except socket.gaierror as exc:
        out["error"] = str(exc)
        out["status"] = "nxdomain"
    except OSError as exc:
        out["error"] = str(exc)
        out["status"] = "error"
    return out


def probe_tls(hostname: str, timeout: float = 5.0) -> dict[str, Any]:
    host = hostname.strip().lower().rstrip(".")
    result: dict[str, Any] = {
        "hostname": host,
        "ok": False,
        "status": "error",
        "expires_at": None,
        "days_left": None,
        "issuer": None,
        "error": None,
    }
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        if not cert:
            result["error"] = "empty certificate"
            return result
        not_after = cert.get("notAfter")
        if not_after:
            # OpenSSL format: 'Jun 15 12:00:00 2027 GMT'
            exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
            result["expires_at"] = exp.isoformat()
            result["days_left"] = (exp - datetime.now(UTC)).days
        issuer = cert.get("issuer") or ()
        for part in issuer:
            for key, val in part:
                if key == "organizationName":
                    result["issuer"] = val
        result["ok"] = True
        result["status"] = "ok" if (result["days_left"] or 0) > 14 else "expiring"
        if (result["days_left"] or 0) <= 0:
            result["status"] = "expired"
    except ssl.SSLCertVerificationError as exc:
        result["error"] = str(exc)
        result["status"] = "invalid"
    except OSError as exc:
        result["error"] = str(exc)
        result["status"] = "unreachable"
    return result


def domain_probe(hostname: str) -> dict[str, Any]:
    dns = resolve_dns(hostname)
    tls = probe_tls(hostname) if dns.get("ok") else {
        "ok": False,
        "status": "skipped",
        "error": "dns failed",
    }
    return {
        "dns": dns,
        "tls": tls,
        "dns_status": dns.get("status", "unknown"),
        "ssl_status": tls.get("status", "unknown"),
    }
