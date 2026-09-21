from __future__ import annotations

import nh3

# Allowlist for Block Factory / legal pages — no scripts, no event handlers.
_ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "button",
    "caption",
    "code",
    "div",
    "em",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "img",
    "input",
    "label",
    "li",
    "main",
    "nav",
    "ol",
    "option",
    "p",
    "section",
    "select",
    "small",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOWED_ATTRIBUTES = {
    # "rel" managed by link_rel= below — do not list it for <a>
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height", "loading", "fetchpriority", "srcset", "sizes"},
    "input": {
        "type",
        "name",
        "value",
        "placeholder",
        "required",
        "data-mask",
        "autocomplete",
        "tabindex",
    },
    "form": {
        "action",
        "method",
        "id",
        "novalidate",
        "data-site-panel-lead-form",
        "data-site-id",
        "data-lead-token",
        "data-endpoint",
    },
    "button": {"type", "name", "value"},
    "label": {"for"},
    "*": {
        "class",
        "id",
        "aria-label",
        "aria-hidden",
        "aria-live",
        "role",
        "data-calc",
        "data-price",
    },
}


def sanitize_html(html: str) -> str:
    """Sanitize untrusted HTML before storing in Block Factory (nh3 / CWE-79)."""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )
