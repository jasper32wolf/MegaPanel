"""Plugin hook dispatcher (TZ 17.12)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.panel import Plugin

logger = logging.getLogger("plugins")

HookFn = Callable[[dict[str, Any]], Awaitable[None] | None]
_HANDLERS: dict[str, list[HookFn]] = {}


def register_hook(event: str, fn: HookFn) -> None:
    _HANDLERS.setdefault(event, []).append(fn)


async def emit_hooks(session: AsyncSession, event: str, payload: dict[str, Any]) -> list[str]:
    """Invoke in-process handlers for enabled plugins that declare the hook."""
    rows = list((await session.execute(select(Plugin).where(Plugin.enabled.is_(True)))).scalars().all())
    fired: list[str] = []
    for plugin in rows:
        hooks = plugin.hooks or []
        if event not in hooks:
            continue
        for fn in _HANDLERS.get(event, []):
            try:
                result = fn(payload)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
                fired.append(f"{plugin.key}:{event}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("hook_failed %s %s: %s", plugin.key, event, exc)
        if not _HANDLERS.get(event):
            # No runtime handler registered — still record that hook point exists
            fired.append(f"{plugin.key}:{event}:noop")
    return fired
