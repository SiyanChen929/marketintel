"""macOS menu-bar companion (rumps): shows the alert count, the latest alerts and shortcuts to the dashboard.

Run with ``python -m market_intel menubar`` while ``serve`` is running (it talks to the local API only).
"""
from __future__ import annotations

import json
import urllib.request
import webbrowser

from . import config
from .alerts import notify_macos


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def run(settings: config.Settings = config.SETTINGS) -> None:
    import rumps

    base = f"http://{settings.host}:{settings.port}"

    class MarketIntelApp(rumps.App):
        def __init__(self):
            super().__init__("MI", quit_button="Quit")
            self.menu = ["Open dashboard", "Poll now", "Pre-market brief", None, rumps.MenuItem("Status: starting"), None]
            self._alert_items: list = []
            self.timer = rumps.Timer(self.refresh, 60)
            self.timer.start()
            self.refresh(None)

        def refresh(self, _):
            try:
                st = _get(f"{base}/api/status")
                al = _get(f"{base}/api/alerts?hours=24&limit=8")["alerts"]
            except Exception as e:
                self.title = "MI ○"
                self.menu["Status: starting"].title = f"Status: server unreachable ({e.__class__.__name__})"
                return
            n_crit = sum(1 for a in al if a["level"] == "critical")
            n_warn = sum(1 for a in al if a["level"] == "warning")
            self.title = f"MI {'●' if n_crit else '◐' if n_warn else '○'} {n_crit + n_warn}" if (n_crit + n_warn) else "MI ○"
            lc = st.get("last_cycle") or {}
            self.menu["Status: starting"].title = f"Last poll {lc.get('at_et', '-')} · {lc.get('new', 0)} new · LLM {'on' if st['llm']['available'] else 'off'}"
            for it in self._alert_items:
                try:
                    del self.menu[it.title]
                except KeyError:
                    pass
            self._alert_items = []
            for a in al:
                label = f"{a['level'][:4].upper()} {a['message'][:70]}"
                item = rumps.MenuItem(label, callback=(lambda _a=a: (lambda _: webbrowser.open(_a["url"]) if _a.get("url") else None))())
                self.menu.add(item)
                self._alert_items.append(item)

        @rumps.clicked("Open dashboard")
        def open_dash(self, _):
            webbrowser.open(base)

        @rumps.clicked("Poll now")
        def poll(self, _):
            try:
                urllib.request.urlopen(urllib.request.Request(f"{base}/api/poll", method="POST"), timeout=5)
                notify_macos("Market Intel", "A polling cycle was started.", sound=False)
            except Exception as e:
                notify_macos("Market Intel", f"Poll failed: {e}", sound=False)

        @rumps.clicked("Pre-market brief")
        def brief(self, _):
            webbrowser.open(base + "#brief")

    MarketIntelApp().run()
