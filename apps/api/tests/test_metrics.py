from __future__ import annotations

from app.services import metrics


def _sample_value(counter, labels: dict[str, str]) -> float:
    for family in counter.collect():
        for sample in family.samples:
            if sample.name.endswith("_total") and sample.labels == labels:
                return sample.value
    return 0.0


def test_delivery_metrics_use_only_bounded_labels():
    labels = {"channel": "unknown", "status": "unknown", "trigger": "unknown"}
    before = _sample_value(metrics._delivery_transitions, labels)

    metrics.record_delivery_transition(
        channel="recipient@example.com",
        status="raw-provider-error",
        trigger="scheduled-job-id-123",
    )

    assert _sample_value(metrics._delivery_transitions, labels) == before + 1


def test_release_and_qa_metrics_record_known_outcomes():
    release_labels = {"action": "publish", "outcome": "blocked"}
    qa_labels = {"verdict": "warn"}
    release_before = _sample_value(metrics._release_transitions, release_labels)
    qa_before = _sample_value(metrics._qa_verdicts, qa_labels)

    metrics.record_release_transition(action="publish", outcome="blocked")
    metrics.record_qa_verdict(verdict="warn")

    assert _sample_value(metrics._release_transitions, release_labels) == release_before + 1
    assert _sample_value(metrics._qa_verdicts, qa_labels) == qa_before + 1
