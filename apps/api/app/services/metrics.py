from __future__ import annotations

from prometheus_client import Counter, Gauge

_DELIVERY_CHANNELS = frozenset({"email", "webhook"})
_DELIVERY_STATUSES = frozenset({"queued", "retrying", "processing", "delivered", "dead_letter"})
_DELIVERY_TRIGGERS = frozenset({"automatic", "manual", "recovery"})
_RELEASE_ACTIONS = frozenset({"build", "publish", "rollback"})
_RELEASE_OUTCOMES = frozenset({"success", "failed", "blocked"})
_QA_VERDICTS = frozenset({"pass", "warn", "block"})

_delivery_transitions = Counter(
    "site_panel_delivery_transitions_total",
    "Durable lead delivery transitions by bounded channel, status and trigger.",
    ("channel", "status", "trigger"),
)
_release_transitions = Counter(
    "site_panel_release_transitions_total",
    "Candidate build and release outcomes by bounded action and outcome.",
    ("action", "outcome"),
)
_qa_verdicts = Counter(
    "site_panel_qa_verdicts_total",
    "Page draft QA verdicts by bounded verdict.",
    ("verdict",),
)
_active_incidents = Gauge(
    "site_panel_active_incidents",
    "Current active operational incidents observed by the panel summary.",
)
_delivery_queue_age = Gauge(
    "site_panel_delivery_queue_age_seconds",
    "Age in seconds of the oldest pending delivery, or zero when none is pending.",
)
_worker_heartbeat_age = Gauge(
    "site_panel_worker_heartbeat_age_seconds",
    "Age in seconds of the persisted worker heartbeat, or zero when not observed.",
)
_worker_heartbeat_status = Gauge(
    "site_panel_worker_heartbeat_status",
    "Persisted worker heartbeat observation status: healthy, stale or not_observed.",
    ("status",),
)


def record_active_incidents(*, count: int) -> None:
    _active_incidents.set(max(0, count))


def record_delivery_queue_age(*, age_seconds: int | None) -> None:
    _delivery_queue_age.set(max(0, age_seconds or 0))


def record_worker_heartbeat(*, age_seconds: int | None, status: str) -> None:
    bounded_status = status if status in {"healthy", "stale", "not_observed"} else "not_observed"
    _worker_heartbeat_age.set(max(0, age_seconds or 0))
    for candidate in ("healthy", "stale", "not_observed"):
        _worker_heartbeat_status.labels(status=candidate).set(float(candidate == bounded_status))


def _bounded(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else "unknown"


def record_delivery_transition(*, channel: str, status: str, trigger: str) -> None:
    _delivery_transitions.labels(
        channel=_bounded(channel, _DELIVERY_CHANNELS),
        status=_bounded(status, _DELIVERY_STATUSES),
        trigger=_bounded(trigger, _DELIVERY_TRIGGERS),
    ).inc()


def record_release_transition(*, action: str, outcome: str) -> None:
    _release_transitions.labels(
        action=_bounded(action, _RELEASE_ACTIONS), outcome=_bounded(outcome, _RELEASE_OUTCOMES)
    ).inc()


def record_qa_verdict(*, verdict: str) -> None:
    _qa_verdicts.labels(verdict=_bounded(verdict, _QA_VERDICTS)).inc()
