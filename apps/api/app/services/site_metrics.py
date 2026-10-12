from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import Site
from app.models.leads import AnalyticsDailyAggregate, AnalyticsEvent, Lead, LeadDeliveryAggregate
from app.models.operations import AlertIncident, SiteMonitorObservation
from app.models.project import LeadOutcome
from app.models.publish import Domain, SitePage
from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

_LOW_SAMPLE_THRESHOLD = 5
_SITE_INCIDENT_CODES = frozenset(
    {
        "site-dns-failure",
        "site-tls-failure",
        "site-tls-expiring",
        "site-unreachable",
        "site-http-error",
    }
)
_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _day(column):
    return func.date(func.timezone("UTC", column))


def _tenant_filter(model, tenant_id: UUID | None):
    return () if tenant_id is None else (model.tenant_id == tenant_id,)


def _series_dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def page_view_series(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    site_id: UUID | None,
    days: int,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(UTC)
    start = now.date() - timedelta(days=days - 1)
    raw_cutoff = _midnight(now.date() - timedelta(days=get_settings().telemetry_raw_retention_days))
    historical_filters = [
        *_tenant_filter(AnalyticsDailyAggregate, tenant_id),
        AnalyticsDailyAggregate.event == "page_view",
        AnalyticsDailyAggregate.event_date >= start,
        AnalyticsDailyAggregate.event_date < raw_cutoff.date(),
    ]
    raw_filters = [
        *_tenant_filter(AnalyticsEvent, tenant_id),
        AnalyticsEvent.source == "first_party",
        AnalyticsEvent.event == "page_view",
        AnalyticsEvent.site_id.is_not(None),
        AnalyticsEvent.created_at >= max(_midnight(start), raw_cutoff),
        AnalyticsEvent.created_at <= now,
    ]
    if site_id is not None:
        historical_filters.append(AnalyticsDailyAggregate.site_id == site_id)
        raw_filters.append(AnalyticsEvent.site_id == site_id)
    historical = (
        select(
            AnalyticsDailyAggregate.event_date.label("day"),
            func.sum(AnalyticsDailyAggregate.event_count).label("page_views"),
            func.sum(AnalyticsDailyAggregate.session_days).label("session_days"),
        )
        .where(*historical_filters)
        .group_by(AnalyticsDailyAggregate.event_date)
    )
    raw_day = _day(AnalyticsEvent.created_at)
    raw = (
        select(
            raw_day.label("day"),
            func.count(AnalyticsEvent.id).label("page_views"),
            func.count(func.distinct(AnalyticsEvent.payload["session"].astext)).label(
                "session_days"
            ),
        )
        .where(*raw_filters)
        .group_by(raw_day)
    )
    combined = union_all(historical, raw).subquery()
    rows = (
        await db.execute(
            select(
                combined.c.day,
                func.sum(combined.c.page_views),
                func.sum(combined.c.session_days),
            ).group_by(combined.c.day)
        )
    ).all()
    totals = {
        item_day: (int(page_views or 0), int(session_days or 0))
        for item_day, page_views, session_days in rows
    }
    return [
        {
            "date": item_day.isoformat(),
            "page_views": totals.get(item_day, (0, 0))[0],
            "consented_session_days": (
                totals[item_day][1]
                if totals.get(item_day, (0, 0))[1] >= _LOW_SAMPLE_THRESHOLD
                else None
            ),
            "low_sample": 0 < totals.get(item_day, (0, 0))[1] < _LOW_SAMPLE_THRESHOLD,
        }
        for item_day in _series_dates(start, now.date())
    ]


async def _count_series(
    db: AsyncSession,
    *,
    model,
    timestamp,
    tenant_id: UUID | None,
    site_id: UUID | None,
    days: int,
    now: datetime,
) -> list[dict]:
    start = now.date() - timedelta(days=days - 1)
    filters = [*_tenant_filter(model, tenant_id), timestamp >= _midnight(start), timestamp <= now]
    if site_id is not None:
        filters.append(model.site_id == site_id)
    event_day = _day(timestamp)
    rows = (
        await db.execute(select(event_day, func.count()).where(*filters).group_by(event_day))
    ).all()
    counts = {item_day: int(count) for item_day, count in rows}
    return [
        {"date": item_day.isoformat(), "count": counts.get(item_day, 0)}
        for item_day in _series_dates(start, now.date())
    ]


async def monitor_error_series(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    site_id: UUID | None,
    days: int,
    now: datetime,
) -> list[dict]:
    start = now.date() - timedelta(days=days - 1)
    filters = [
        *_tenant_filter(SiteMonitorObservation, tenant_id),
        SiteMonitorObservation.observed_at >= _midnight(start),
        SiteMonitorObservation.observed_at <= now,
        SiteMonitorObservation.signal_code.is_not(None),
    ]
    if site_id is not None:
        filters.append(SiteMonitorObservation.site_id == site_id)
    observation_day = _day(SiteMonitorObservation.observed_at)
    rows = (
        await db.execute(
            select(observation_day, func.count()).where(*filters).group_by(observation_day)
        )
    ).all()
    counts = {item_day: int(count) for item_day, count in rows}
    return [
        {"date": item_day.isoformat(), "count": counts.get(item_day, 0)}
        for item_day in _series_dates(start, now.date())
    ]


async def _site_page_summary(
    db: AsyncSession, *, tenant_id: UUID | None, site_ids: list[UUID]
) -> dict[UUID, dict]:
    if not site_ids:
        return {}
    rows = (
        await db.execute(
            select(
                SitePage.site_id,
                func.count(SitePage.id),
                func.count(SitePage.id).filter(SitePage.index_state == "indexed"),
                func.count(SitePage.id).filter(SitePage.index_state != "indexed"),
                func.count(SitePage.id).filter(SitePage.thin.is_(True)),
            )
            .where(*_tenant_filter(SitePage, tenant_id), SitePage.site_id.in_(site_ids))
            .group_by(SitePage.site_id)
        )
    ).all()
    return {
        site_id: {
            "urls": int(urls),
            "indexed_urls": int(indexed),
            "noindex_urls": int(noindex),
            "thin_urls": int(thin),
        }
        for site_id, urls, indexed, noindex, thin in rows
    }


async def _page_views_by_site(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    site_ids: list[UUID],
    days: int,
    now: datetime,
) -> dict[UUID, int]:
    if not site_ids:
        return {}
    start = now.date() - timedelta(days=days - 1)
    raw_cutoff = _midnight(now.date() - timedelta(days=get_settings().telemetry_raw_retention_days))
    historical = select(
        AnalyticsDailyAggregate.site_id.label("site_id"),
        AnalyticsDailyAggregate.event_count.label("views"),
    ).where(
        *_tenant_filter(AnalyticsDailyAggregate, tenant_id),
        AnalyticsDailyAggregate.site_id.in_(site_ids),
        AnalyticsDailyAggregate.event == "page_view",
        AnalyticsDailyAggregate.event_date >= start,
        AnalyticsDailyAggregate.event_date < raw_cutoff.date(),
    )
    recent = (
        select(
            AnalyticsEvent.site_id.label("site_id"),
            func.count(AnalyticsEvent.id).label("views"),
        )
        .where(
            *_tenant_filter(AnalyticsEvent, tenant_id),
            AnalyticsEvent.site_id.in_(site_ids),
            AnalyticsEvent.source == "first_party",
            AnalyticsEvent.event == "page_view",
            AnalyticsEvent.created_at >= max(_midnight(start), raw_cutoff),
            AnalyticsEvent.created_at <= now,
        )
        .group_by(AnalyticsEvent.site_id)
    )
    combined = union_all(historical, recent).subquery()
    rows = (
        await db.execute(
            select(combined.c.site_id, func.sum(combined.c.views)).group_by(combined.c.site_id)
        )
    ).all()
    return {site_id: int(views) for site_id, views in rows}


async def _top_pages(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    site_id: UUID,
    days: int,
    now: datetime,
) -> list[dict]:
    start = now.date() - timedelta(days=days - 1)
    raw_cutoff = _midnight(now.date() - timedelta(days=get_settings().telemetry_raw_retention_days))
    historical = select(
        AnalyticsDailyAggregate.path.label("path"),
        AnalyticsDailyAggregate.event_count.label("views"),
        AnalyticsDailyAggregate.session_days.label("sessions"),
    ).where(
        *_tenant_filter(AnalyticsDailyAggregate, tenant_id),
        AnalyticsDailyAggregate.site_id == site_id,
        AnalyticsDailyAggregate.event == "page_view",
        AnalyticsDailyAggregate.event_date >= start,
        AnalyticsDailyAggregate.event_date < raw_cutoff.date(),
    )
    raw = (
        select(
            AnalyticsEvent.path.label("path"),
            func.count(AnalyticsEvent.id).label("views"),
            func.count(func.distinct(AnalyticsEvent.payload["session"].astext)).label("sessions"),
        )
        .where(
            *_tenant_filter(AnalyticsEvent, tenant_id),
            AnalyticsEvent.site_id == site_id,
            AnalyticsEvent.source == "first_party",
            AnalyticsEvent.event == "page_view",
            AnalyticsEvent.created_at >= max(_midnight(start), raw_cutoff),
            AnalyticsEvent.created_at <= now,
        )
        .group_by(AnalyticsEvent.path, _day(AnalyticsEvent.created_at))
    )
    combined = union_all(historical, raw).subquery()
    rows = (
        await db.execute(
            select(
                combined.c.path,
                func.sum(combined.c.views),
                func.sum(combined.c.sessions),
            )
            .group_by(combined.c.path)
            .order_by(func.sum(combined.c.views).desc(), combined.c.path)
            .limit(20)
        )
    ).all()
    return [
        {
            "path": path,
            "page_views": int(views) if sessions >= _LOW_SAMPLE_THRESHOLD else None,
            "consented_session_days": int(sessions) if sessions >= _LOW_SAMPLE_THRESHOLD else None,
            "low_sample": sessions < _LOW_SAMPLE_THRESHOLD,
        }
        for path, views, sessions in rows
    ]


async def site_metrics_report(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    days: int,
    selected_site_id: UUID | None,
) -> dict:
    now = datetime.now(UTC)
    sites = list(
        (
            await db.execute(
                select(Site).where(*_tenant_filter(Site, tenant_id)).order_by(Site.domain)
            )
        )
        .scalars()
        .all()
    )
    site_ids = [site.id for site in sites]
    if selected_site_id is not None and selected_site_id not in site_ids:
        return {"sites": [], "selected_site": None}
    domains = (
        list(
            (
                await db.execute(
                    select(Domain).where(
                        *_tenant_filter(Domain, tenant_id), Domain.site_id.in_(site_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if site_ids
        else []
    )
    domains_by_site: dict[UUID, list[Domain]] = {}
    domain_ids: set[str] = set()
    for domain in domains:
        if domain.site_id is not None:
            domains_by_site.setdefault(domain.site_id, []).append(domain)
            domain_ids.add(str(domain.id))
    incident_rows = (
        list(
            (
                await db.execute(
                    select(AlertIncident).where(
                        *_tenant_filter(AlertIncident, tenant_id),
                        AlertIncident.subject_kind == "domain",
                        AlertIncident.subject_key.in_(domain_ids) if domain_ids else False,
                    )
                )
            )
            .scalars()
            .all()
        )
        if domain_ids
        else []
    )
    incidents_by_domain: dict[str, list[AlertIncident]] = {}
    for incident in incident_rows:
        if incident.signal_code in _SITE_INCIDENT_CODES and incident.status in {
            "open",
            "acknowledged",
        }:
            incidents_by_domain.setdefault(incident.subject_key, []).append(incident)
    page_summary = await _site_page_summary(db, tenant_id=tenant_id, site_ids=site_ids)
    traffic = await page_view_series(db, tenant_id=tenant_id, site_id=None, days=days, now=now)
    leads = await _count_series(
        db,
        model=Lead,
        timestamp=Lead.created_at,
        tenant_id=tenant_id,
        site_id=None,
        days=days,
        now=now,
    )
    outcomes = await _count_series(
        db,
        model=LeadOutcome,
        timestamp=LeadOutcome.created_at,
        tenant_id=tenant_id,
        site_id=None,
        days=days,
        now=now,
    )
    monitor_errors = await monitor_error_series(
        db, tenant_id=tenant_id, site_id=None, days=days, now=now
    )
    lead_counts = dict(
        (
            await db.execute(
                select(Lead.site_id, func.count(Lead.id))
                .where(
                    *_tenant_filter(Lead, tenant_id),
                    Lead.created_at >= _midnight(now.date() - timedelta(days=days - 1)),
                    Lead.created_at <= now,
                )
                .group_by(Lead.site_id)
            )
        ).all()
    )
    attention_counts = dict(
        (
            await db.execute(
                select(Lead.site_id, func.count(LeadDeliveryAggregate.lead_id))
                .join(Lead, Lead.id == LeadDeliveryAggregate.lead_id)
                .where(
                    *_tenant_filter(LeadDeliveryAggregate, tenant_id),
                    LeadDeliveryAggregate.status == "attention",
                )
                .group_by(Lead.site_id)
            )
        ).all()
    )
    traffic_by_site = await _page_views_by_site(
        db,
        tenant_id=tenant_id,
        site_ids=site_ids,
        days=days,
        now=now,
    )
    sites_payload = []
    for site in sites:
        site_domains = domains_by_site.get(site.id, [])
        open_incidents = [
            incident
            for domain in site_domains
            for incident in incidents_by_domain.get(str(domain.id), [])
        ]
        severity = max(
            (incident.severity for incident in open_incidents),
            key=lambda value: _SEVERITY_RANK.get(value, 0),
            default=None,
        )
        dns_states = {domain.dns_status for domain in site_domains}
        tls_states = {domain.ssl_status for domain in site_domains}
        latest_observed_at = max(
            (domain.checked_at for domain in site_domains if domain.checked_at is not None),
            default=None,
        )
        health = (
            "error"
            if open_incidents or "error" in dns_states or "error" in tls_states
            else "warning"
            if "expiring" in tls_states or "pending" in tls_states or "unknown" in dns_states
            else "ok"
            if site_domains
            else "not_observed"
        )
        sites_payload.append(
            {
                "site_id": str(site.id),
                "project_id": str(site.project_id) if site.project_id else None,
                "domain": site.domain,
                "publish_state": site.publish_state,
                "page_views": int(traffic_by_site.get(site.id, 0)),
                "leads": int(lead_counts.get(site.id, 0)),
                "delivery_attention": int(attention_counts.get(site.id, 0)),
                "health": {
                    "state": health,
                    "dns_status": sorted(dns_states)[0] if dns_states else None,
                    "ssl_status": sorted(tls_states)[0] if tls_states else None,
                    "last_observed_at": (
                        latest_observed_at.isoformat() if latest_observed_at else None
                    ),
                    "open_incidents": len(open_incidents),
                    "worst_severity": severity,
                },
                "seo": page_summary.get(
                    site.id,
                    {"urls": 0, "indexed_urls": 0, "noindex_urls": 0, "thin_urls": 0},
                ),
            }
        )
    selected = next((site for site in sites if site.id == selected_site_id), None)
    selected_payload = None
    if selected is not None:
        selected_payload = {
            "site_id": str(selected.id),
            "domain": selected.domain,
            "traffic": await page_view_series(
                db, tenant_id=tenant_id, site_id=selected.id, days=days, now=now
            ),
            "leads": await _count_series(
                db,
                model=Lead,
                timestamp=Lead.created_at,
                tenant_id=tenant_id,
                site_id=selected.id,
                days=days,
                now=now,
            ),
            "monitor_errors": await monitor_error_series(
                db, tenant_id=tenant_id, site_id=selected.id, days=days, now=now
            ),
            "top_pages": await _top_pages(
                db, tenant_id=tenant_id, site_id=selected.id, days=days, now=now
            ),
            "seo": page_summary.get(
                selected.id,
                {"urls": 0, "indexed_urls": 0, "noindex_urls": 0, "thin_urls": 0},
            ),
        }
    total_page_views = sum(point["page_views"] for point in traffic)
    return {
        "observed_at": now.isoformat(),
        "days": days,
        "privacy": {
            "consent_required": True,
            "ip_stored": False,
            "low_sample_threshold": _LOW_SAMPLE_THRESHOLD,
            "session_metric": "sum_of_daily_distinct_sessions",
            "raw_retention_days": get_settings().telemetry_raw_retention_days,
            "aggregate_retention_days": get_settings().telemetry_aggregate_retention_days,
        },
        "totals": {
            "page_views": total_page_views,
            "leads": sum(point["count"] for point in leads),
            "outcomes": sum(point["count"] for point in outcomes),
            "sites_with_errors": sum(site["health"]["state"] == "error" for site in sites_payload),
            "delivery_attention": sum(site["delivery_attention"] for site in sites_payload),
        },
        "traffic": traffic,
        "leads": leads,
        "outcomes": outcomes,
        "monitor_errors": monitor_errors,
        "sites": sites_payload,
        "selected_site": selected_payload,
        "search_data": {
            "google_search_console": "not_connected",
            "yandex_webmaster": "not_connected",
        },
    }
