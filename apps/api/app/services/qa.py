from __future__ import annotations

from app.services.dedup import compare_texts
from site_panel_shared.manifests import PageManifest


def run_page_qa(*, page_manifest: dict, input_snapshot: dict, existing_texts: list[str]) -> dict:
    page = PageManifest.model_validate(page_manifest)
    facts = input_snapshot.get("facts") or {}
    keywords = (input_snapshot.get("keyword_snapshot") or {}).get("items") or []
    findings: list[dict[str, str]] = []
    service = str(facts.get("service") or "").strip()
    services = facts.get("services") or []
    if not service and not any(isinstance(item, str) and item.strip() for item in services):
        findings.append(
            {
                "verdict": "block",
                "rule": "required_service",
                "evidence": "Confirm a service in business facts",
            }
        )
    contacts = facts.get("contacts") or {}
    if not isinstance(contacts, dict) or not str(contacts.get("phone") or "").strip():
        findings.append(
            {
                "verdict": "block",
                "rule": "required_contact",
                "evidence": "Confirm a phone in business facts",
            }
        )
    if not input_snapshot.get("project_domain"):
        findings.append(
            {"verdict": "block", "rule": "required_domain", "evidence": "Set a project domain"}
        )
    if not (input_snapshot.get("geo_snapshot") or {}).get("items"):
        findings.append(
            {
                "verdict": "block",
                "rule": "required_geo",
                "evidence": "Select a primary geographic place",
            }
        )
    if not page.slug.startswith("/") or "//" in page.slug:
        findings.append(
            {"verdict": "block", "rule": "safe_slug", "evidence": "Use a normalized unique slug"}
        )
    rendered_text = " ".join(
        [
            page.title_template,
            page.h1_template,
            page.meta_description_template,
            page.unique_core or "",
        ]
    )
    if any(
        compare_texts(rendered_text, existing) > 0.85 for existing in existing_texts if existing
    ):
        findings.append(
            {
                "verdict": "block",
                "rule": "duplicate_content",
                "evidence": "Candidate is too similar to an existing draft",
            }
        )
    if not keywords:
        findings.append(
            {
                "verdict": "warn",
                "rule": "keyword_coverage",
                "evidence": "No approved keywords are linked to this page",
            }
        )
    if len(page.title_template) > 70:
        findings.append(
            {
                "verdict": "warn",
                "rule": "title_length",
                "evidence": "Title template exceeds 70 characters",
            }
        )
    if len(page.meta_description_template) > 170:
        findings.append(
            {
                "verdict": "warn",
                "rule": "meta_length",
                "evidence": "Meta description exceeds 170 characters",
            }
        )
    if len(rendered_text) < 120:
        findings.append(
            {
                "verdict": "warn",
                "rule": "thin_content",
                "evidence": "Candidate needs more approved content before publication",
            }
        )
    verdict = (
        "block"
        if any(item["verdict"] == "block" for item in findings)
        else "warn"
        if findings
        else "pass"
    )
    return {"verdict": verdict, "findings": findings}
