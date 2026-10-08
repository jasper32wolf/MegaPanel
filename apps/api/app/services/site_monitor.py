from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import get_settings
from app.models import AlertIncident, Domain, Site
from app.services.operations import observe_alert
from app.services.operator_alerts import create_operator_alert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SiteProbe:
    dns_status: str
    ssl_status: str
    http_status: int | None
    signal_code: str | None


def _global_addresses(hostname: str) -> list[str]:
    addresses: list[str] = []
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        return addresses
    for _family, _type, _protocol, _canonical, sockaddr in infos:
        address = str(sockaddr[0])
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_global and address not in addresses:
            addresses.append(address)
    return addresses


async def probe_published_domain(hostname: str) -> SiteProbe:
    host = hostname.strip().lower().rstrip(".")
    addresses = await asyncio.to_thread(_global_addresses, host)
    if not addresses:
        return SiteProbe("error", "skipped", None, "site-dns-failure")
    settings = get_settings()
    context = ssl.create_default_context()
    writer = None
    tls_signal: str | None = None
    tls_status = "ok"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                addresses[0],
                443,
                ssl=context,
                server_hostname=host,
            ),
            timeout=settings.site_monitor_timeout_seconds,
        )
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert() if ssl_object else None
        expires_at = certificate.get("notAfter") if certificate else None
        if expires_at:
            try:
                parsed_expiry = datetime.strptime(expires_at, "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=UTC
                )
            except ValueError:
                return SiteProbe("ok", "invalid", None, "site-tls-failure")
            if parsed_expiry <= datetime.now(UTC):
                return SiteProbe("ok", "expired", None, "site-tls-failure")
            if (parsed_expiry - datetime.now(UTC)).days <= 14:
                tls_status = "expiring"
                tls_signal = "site-tls-expiring"
        request = (
            f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
            "User-Agent: SitePanelMonitor/1.0\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(
            reader.read(4096), timeout=settings.site_monitor_timeout_seconds
        )
        first_line = response.split(b"\r\n", 1)[0].split()
        if len(first_line) < 2 or not first_line[1].isdigit():
            return SiteProbe("ok", tls_status, None, "site-unreachable")
        status = int(first_line[1])
        if status == 404 or status >= 500:
            return SiteProbe("ok", tls_status, status, "site-http-error")
        if 200 <= status < 400:
            return SiteProbe("ok", tls_status, status, tls_signal)
        return SiteProbe("ok", tls_status, status, "site-unreachable")
    except ssl.SSLError:
        return SiteProbe("ok", "invalid", None, "site-tls-failure")
    except (TimeoutError, OSError):
        return SiteProbe("ok", "unreachable", None, "site-unreachable")
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def _observe_domain_signal(
    db: AsyncSession,
    *,
    domain: Domain,
    signal_code: str,
    active: bool,
) -> None:
    subject_key = str(domain.id)
    existing = (
        await db.execute(
            select(AlertIncident.id).where(
                AlertIncident.tenant_id == domain.tenant_id,
                AlertIncident.signal_code == signal_code,
                AlertIncident.subject_kind == "domain",
                AlertIncident.subject_key == subject_key,
                AlertIncident.status.in_(("open", "acknowledged")),
            )
        )
    ).scalar_one_or_none()
    await observe_alert(
        db,
        tenant_id=domain.tenant_id,
        signal_code=signal_code,
        active=active,
        subject_kind="domain",
        subject_key=subject_key,
    )
    if active and existing is None:
        create_operator_alert(
            db,
            tenant_id=domain.tenant_id,
            category="site",
            signal_code=signal_code,
            title="Проблема опубликованного сайта",
            body="Проверка сайта требует внимания. Откройте раздел доменов и инцидентов.",
            subject_kind="domain",
            subject_key=subject_key,
        )
    elif not active and existing is not None:
        create_operator_alert(
            db,
            tenant_id=domain.tenant_id,
            category="site",
            signal_code=f"{signal_code}-recovered",
            title="Сайт снова доступен",
            body="Проверка домена снова проходит успешно.",
            subject_kind="domain",
            subject_key=subject_key,
        )


async def monitor_published_domains(db: AsyncSession) -> int:
    rows = list(
        (
            await db.execute(
                select(Domain, Site)
                .join(Site, Site.id == Domain.site_id)
                .where(Site.publish_state == "published")
                .order_by(Domain.created_at, Domain.id)
            )
        ).all()
    )
    for domain, _site in rows:
        probe = await probe_published_domain(domain.hostname)
        domain.dns_status = probe.dns_status
        domain.ssl_status = probe.ssl_status
        monitored = (
            "site-dns-failure",
            "site-tls-failure",
            "site-tls-expiring",
            "site-unreachable",
            "site-http-error",
        )
        for signal_code in monitored:
            await _observe_domain_signal(
                db,
                domain=domain,
                signal_code=signal_code,
                active=(
                    probe.signal_code == signal_code
                    or (signal_code == "site-tls-expiring" and probe.ssl_status == "expiring")
                ),
            )
    if rows:
        await db.commit()
    return len(rows)
