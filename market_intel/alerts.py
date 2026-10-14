"""Alert generation and macOS desktop notifications."""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone

from . import config
from .entities import EntityIndex
from .store import Store

log = logging.getLogger("market_intel")
ARROW = {2: "▲▲", 1: "▲", 0: "•", -1: "▼", -2: "▼▼"}


def alerts_for_event(event: dict, index: EntityIndex) -> list[dict]:
    """One alert per event for its primary ticker (held names first). Only warning/critical are notified."""
    level = event.get("alert_level")
    if level not in ("warning", "critical", "info"):
        return []
    tickers = list(event.get("tickers") or [])
    in_title = set(index.match(event.get("title") or ""))
    held = sorted((t for t in tickers if t in index.companies and index.companies[t].is_holding), key=lambda t: (t not in in_title, tickers.index(t)))
    primary = held[0] if held else (tickers[0] if tickers else None)
    label = event.get("one_liner") or event["title"]
    tag = f"{primary} " if primary else ""
    msg = f"{tag}{ARROW.get(int(event.get('direction', 0)), '•')} {label} [{event.get('source_name', '')}]"
    return [{"level": level, "ticker": primary, "event_id": event["id"], "message": msg[:400], "created": datetime.now(timezone.utc)}]


def notify_macos(title: str, message: str, subtitle: str | None = None, sound: bool = True) -> bool:
    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message[:230])}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle[:80])}"'
    if sound:
        script += ' sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
        return True
    except Exception as e:                        # pragma: no cover - platform
        log.warning("notification failed: %s", e)
        return False


def dispatch(store: Store, settings: config.Settings = config.SETTINGS, dry_run: bool = False) -> int:
    """Send pending alerts at or above the configured level (grouped when there are many). Returns the count sent."""
    pending = store.alerts(unnotified=True, limit=200)
    min_rank = config.LEVEL_RANK.get(settings.notify_min_level, 1)
    to_send = [a for a in pending if config.LEVEL_RANK.get(a["level"], 0) >= min_rank]
    sent = 0
    if settings.notify and to_send and not dry_run:
        to_send.sort(key=lambda a: (-config.LEVEL_RANK.get(a["level"], 0), -a["id"]))
        head = to_send[: settings.notify_max_per_cycle] if len(to_send) <= settings.notify_max_per_cycle else to_send[: settings.notify_max_per_cycle - 1]
        for a in head:
            notify_macos(f"Market Intel · {a['level'].upper()}", a["message"], subtitle=a.get("ticker") or a.get("event_type") or None, sound=a["level"] == "critical")
            sent += 1
        rest = len(to_send) - len(head)
        if rest > 0:
            notify_macos("Market Intel", f"{rest} more alert(s) - open the dashboard (http://{settings.host}:{settings.port})", sound=False)
    store.mark_notified([a["id"] for a in pending])       # info-level alerts are recorded, not pushed
    return sent
