"""Egress allowlist + FinOps cost tracking helpers (TZ 12.3 / 15)."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.core.config import get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_ALLOWLIST = {
    "api.deepseek.com",
    "api.anthropic.com",
    "api.openai.com",
    "open.bigmodel.cn",
    "openrouter.ai",
    "api.groq.com",
    "generativelanguage.googleapis.com",
    "nominatim.openstreetmap.org",
    "api.indexnow.org",
    "yandex.com",
    "www.bing.com",
    "api.dataforseo.com",
    "hooks.amocrm.ru",
    "oauth.bitrix.info",
    "api.telegram.org",
}


@dataclass
class EgressGuard:
    allowlist: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWLIST))

    def __post_init__(self) -> None:
        configured = get_settings().ai_endpoint_allowlist
        self.allowlist.update(
            item.strip().lower().rstrip(".") for item in configured.split(",") if item.strip()
        )

    def check(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        host = host.lower()
        if host in self.allowlist:
            return True
        return any(host.endswith("." + parent) for parent in self.allowlist)

    def assert_safe_endpoint(self, url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise PermissionError(
                "Provider endpoint must be HTTPS without credentials or query data"
            )
        host = parsed.hostname.lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise PermissionError(
                "Provider endpoint must not target a private or link-local address"
            )
        return host

    async def assert_public_dns(self, url: str) -> None:
        host = self.assert_safe_endpoint(url)
        if self._is_ip_address(host):
            return
        import asyncio
        import socket

        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                host, urlparse(url).port or 443, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise PermissionError("Provider endpoint hostname did not resolve") from exc
        if not records:
            raise PermissionError("Provider endpoint hostname did not resolve")
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                raise PermissionError("Provider endpoint resolves to a non-public address")

    @staticmethod
    def _is_ip_address(host: str) -> bool:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True

    def assert_allowed(self, url: str) -> None:
        self.assert_safe_endpoint(url)
        if not self.check(url):
            raise PermissionError(f"Egress blocked for host in URL: {url}")


@dataclass
class FinOpsLedger:
    """Process-local mirror; prefer record_finops() for durable writes."""

    entries: list[dict] = field(default_factory=list)

    def add(self, tenant_id: str, kind: str, amount_usd: float, meta: dict | None = None) -> None:
        self.entries.append(
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "amount_usd": amount_usd,
                "meta": meta or {},
            }
        )

    def total_for(self, tenant_id: str) -> float:
        return sum(e["amount_usd"] for e in self.entries if e["tenant_id"] == tenant_id)

    def by_kind(self, tenant_id: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self.entries:
            if e["tenant_id"] != tenant_id:
                continue
            out[e["kind"]] = out.get(e["kind"], 0.0) + e["amount_usd"]
        return out


async def record_finops(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    kind: str,
    amount_usd: float,
    meta: dict | None = None,
) -> None:
    from app.models import FinopsEntry

    tid = uuid.UUID(str(tenant_id))
    session.add(
        FinopsEntry(
            tenant_id=tid,
            kind=kind,
            amount_usd=amount_usd,
            meta=meta or {},
        )
    )


async def finops_summary(session: AsyncSession, tenant_id: uuid.UUID | str) -> dict:
    from app.models import FinopsEntry

    tid = uuid.UUID(str(tenant_id))
    rows = list(
        (await session.execute(select(FinopsEntry).where(FinopsEntry.tenant_id == tid)))
        .scalars()
        .all()
    )
    by_kind: dict[str, float] = {}
    total = 0.0
    for row in rows:
        by_kind[row.kind] = by_kind.get(row.kind, 0.0) + float(row.amount_usd)
        total += float(row.amount_usd)
    return {
        "tenant_id": str(tid),
        "total_usd": round(total, 6),
        "by_kind": {k: round(v, 6) for k, v in by_kind.items()},
        "entries": len(rows),
        "alerts": ["llm_spike"] if by_kind.get("llm", 0) > 100 else [],
    }


finops = FinOpsLedger()
egress = EgressGuard()
