from __future__ import annotations

from prometheus_client import Counter

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
