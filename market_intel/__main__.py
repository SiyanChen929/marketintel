"""CLI entry point: python -m market_intel <command>."""
from __future__ import annotations

import argparse
import json
import sys

from . import config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="market_intel", description="Market Intelligence AI - real-time event monitor for the AI/semi stock-selection process")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the poller and the local dashboard/API"); s.add_argument("--port", type=int); s.add_argument("--no-poll", action="store_true"); s.add_argument("--no-notify", action="store_true")
    p = sub.add_parser("poll", help="run one polling cycle"); p.add_argument("--no-notify", action="store_true"); p.add_argument("--json", action="store_true")
    a = sub.add_parser("ask", help="ask a question over the event store"); a.add_argument("question", nargs="+")
    b = sub.add_parser("brief", help="build the pre-market brief now"); b.add_argument("--no-notify", action="store_true")
    sub.add_parser("overlay", help="recompute ticker scores and write outputs/latest/event_overlay.csv")
    sub.add_parser("apply-cap", help="withhold rule-engine read-through above readthrough_cap for stored events (one-off after S7)")
    bs = sub.add_parser("backfill-stories", help="cluster stored events into stories (one-off after the S6 migration)"); bs.add_argument("--days", type=int, default=90)
    sub.add_parser("status", help="print poller/LLM/store status")
    se = sub.add_parser("search", help="full-text search of stored events"); se.add_argument("query", nargs="+")
    sub.add_parser("menubar", help="run the macOS menu-bar companion (rumps)")
    sub.add_parser("notify-test", help="send a test desktop notification")
    sk = sub.add_parser("set-key", help="store an Anthropic or OpenAI API key (hidden prompt, auto-detected by prefix) and validate it"); sk.add_argument("--provider", choices=["anthropic", "openai"])
    sub.add_parser("calendar", help="print the macro/earnings calendar for the next 7 days")
    tk = sub.add_parser("token", help="print the access token used for internet access (login page / Bearer)"); tk.add_argument("--reset", action="store_true")
    sub.add_parser("url", help="print the local and public (tunnel) URLs")
    ac = sub.add_parser("account", help="manage named accounts (roles: viewer analyst pm risk quantops admin)")
    ac.add_argument("op", choices=["add", "list", "disable"]); ac.add_argument("username", nargs="?"); ac.add_argument("--role", default="analyst"); ac.add_argument("--name", default=None)
    au = sub.add_parser("audit", help="show the append-only audit log and verify its hash chain"); au.add_argument("--object-type"); au.add_argument("--object-id"); au.add_argument("--limit", type=int, default=30)
    sm = sub.add_parser("set-model", help="pin the LLM model (validated with a test call) or --auto to return to the provider default"); sm.add_argument("model", nargs="?"); sm.add_argument("--auto", action="store_true")
    args = ap.parse_args(argv)

    settings = config.SETTINGS
    if args.cmd == "serve":
        if args.port:
            settings.port = args.port
        if args.no_notify:
            settings.notify = False
        from .server import serve
        serve(settings, poll=not args.no_poll)
        return 0
    if args.cmd == "poll":
        from .poller import Poller, setup_logging
        setup_logging()
        if args.no_notify:
            settings.notify = False
        st = Poller(settings).cycle()
        if args.json:
            print(json.dumps(st, indent=1, default=str))
        return 0
    if args.cmd == "ask":
        from .ask import answer
        from .classify.llm import LLM
        from .entities import build_index
        from .store import Store
        ix = build_index()
        for chunk in answer(" ".join(args.question), Store(), ix, LLM(settings, ix), stream=True):
            sys.stdout.write(chunk); sys.stdout.flush()
        print()
        return 0
    if args.cmd == "brief":
        from .brief import build
        from .classify.llm import LLM
        from .entities import build_index
        from .store import Store
        ix = build_index()
        print(build(Store(), ix, settings, LLM(settings, ix), notify=not args.no_notify))
        return 0
    if args.cmd == "apply-cap":
        from .store import Store
        n = Store().apply_readthrough_cap(settings.readthrough_cap)
        print(f"read-through withheld on {n} events (cap {settings.readthrough_cap}); they are flagged needs_review")
        return 0
    if args.cmd == "backfill-stories":
        from .store import Store
        st = Store()
        n = st.backfill_stories(args.days)
        top = st.conn.execute("SELECT story_id, n_events, n_sources, n_independent, status, substr(title,1,70) t FROM stories ORDER BY n_events DESC LIMIT 8").fetchall()
        print(f"assigned {n} events; stories: {st.conn.execute('SELECT COUNT(*) FROM stories').fetchone()[0]}")
        for r in top:
            print(f"  x{r['n_events']:<3d} {r['n_sources']:>2d} outlets {r['n_independent']} indep {r['status']:9s} {r['t']}")
        return 0
    if args.cmd == "overlay":
        from . import scoring
        from .entities import build_index
        from .store import Store
        ix, store = build_index(), Store()
        summary = scoring.write_overlay(scoring.compute_scores(store, ix, settings), store, ix, settings)
        print(json.dumps({k: summary[k] for k in ("asof_et", "n_holdings_flagged", "csv")}, indent=1))
        for h in summary["holdings"]:
            print(f"  {h['ticker']:6s} w={h['weight']:.2f} 7d={h['event_score_7d']:+.2f} n={h['n_events_7d']:2d} {h['alert_level'] or '-':8s} {h['action']:13s} {h['last_event_title'] or ''}")
        return 0
    if args.cmd == "status":
        from .classify.llm import LLM
        from .store import Store
        store = Store()
        _l = LLM(settings)
        print(json.dumps({"llm_available": _l.available, "provider": _l.provider, "model": _l.model or "(auto)", "db": store.stats(), "last_cycle": store.kv_get("last_cycle"),
                          "holdings": list(config.holdings())}, indent=1, default=str))
        return 0
    if args.cmd == "search":
        from .store import Store
        for e in Store().search(" ".join(args.query)):
            print(f"{e['published'][:16]} {e['event_type']:20s} {','.join(e['tickers']):12s} {e['title'][:90]}")
        return 0
    if args.cmd == "menubar":
        from .menubar import run
        run(settings)
        return 0
    if args.cmd == "set-key":
        import getpass
        import os
        from .classify.llm import LLM, detect_provider
        key = getpass.getpass("Paste your Anthropic or OpenAI API key (input hidden): ").strip()
        if not key:
            print("no key entered"); return 1
        prov = args.provider or detect_provider(key)
        p = config.PROJECT_ROOT / (config.ANTHROPIC_KEY_FILES[0] if prov == "anthropic" else config.OPENAI_KEY_FILES[0])
        p.write_text(key + "\n"); p.chmod(0o600)
        os.environ["ANTHROPIC_API_KEY" if prov == "anthropic" else "OPENAI_API_KEY"] = key
        llm = LLM(settings, provider=prov)
        ok = llm.ping()
        print(f"saved to {p} (mode 600) - provider {prov}, model {llm.model}")
        print("validated: the model answered - restart the service with: launchctl kickstart -k gui/$(id -u)/com.siyanchen.marketintel" if ok
              else f"saved, but the test call failed ({llm.disabled_reason or 'see log'}) - check the key, billing/credits and the model access")
        return 0 if ok else 1
    if args.cmd == "notify-test":
        from .alerts import notify_macos
        print("sent" if notify_macos("Market Intel", "Desktop notifications are working.", subtitle="test") else "failed")
        return 0
    if args.cmd == "token":
        from . import auth
        print(auth.reset_token() if args.reset else auth.get_token())
        return 0
    if args.cmd == "set-model":
        from .classify.llm import LLM
        if args.auto or not args.model:
            config.save_user_setting("model", None)
            print("model: provider default (auto). Restart the service: launchctl kickstart -k gui/$(id -u)/com.siyanchen.marketintel")
            return 0
        llm = LLM(config.Settings(model=args.model))
        if not llm.available:
            print("no LLM key configured (python -m market_intel set-key)"); return 1
        ok = llm.ping()
        if not ok:
            print(f"model {args.model} did not answer ({llm.disabled_reason or 'see log'}); not saved"); return 1
        config.save_user_setting("model", args.model)
        print(f"model pinned: {args.model} ({llm.provider}) -> {config.SETTINGS_FILE}. Restart the service: launchctl kickstart -k gui/$(id -u)/com.siyanchen.marketintel")
        return 0
    if args.cmd == "account":
        from .identity import ROLES, create_account, deactivate_account, list_accounts
        if args.op == "list":
            rows = list_accounts()
            if not rows:
                print("no accounts yet - every request falls back to the local operator or the shared token")
            for r in rows:
                print(f"  {r['username']:16s} {r['role']:9s} {'active' if r['active'] else 'disabled':8s} {r['display_name']}")
            return 0
        if not args.username:
            print("username required"); return 1
        if args.op == "add":
            if args.role not in ROLES:
                print(f"role must be one of {', '.join(ROLES)}"); return 1
            who, token = create_account(args.username, args.name or args.username, args.role)
            print(f"created {who.username} ({who.role})\n  token: {token}\n  give this to that person only; it is not recoverable and identifies them in the audit log")
            return 0
        print("disabled" if deactivate_account(args.username) else "no such account")
        return 0
    if args.cmd == "audit":
        from .identity import audit_log, verify_chain
        chain = verify_chain()
        print(f"chain: {'OK' if chain['ok'] else 'BROKEN at seq ' + str(chain.get('broken_at_seq'))} · {chain['n_events']} events")
        for e in audit_log(args.object_type, args.object_id, limit=args.limit):
            print(f"  {e['at']}  {e['actor']:12s} {e['role']:9s} {e['action']:16s} {e['object_type']}/{e['object_id']}")
        return 0
    if args.cmd == "url":
        from . import tunnel
        t = tunnel.status()
        print(f"local:  http://{settings.host}:{settings.port}")
        print(f"public: {t['public_url'] or '(tunnel not running - ./scripts/install_market_intel.sh --tunnel)'}")
        return 0
    if args.cmd == "calendar":
        from .store import Store
        from datetime import timedelta
        today = config.now_et().date()
        for c in Store().calendar(today.isoformat(), (today + timedelta(days=7)).isoformat()):
            print(f"{c['date']} {c['time_et'] or '     ':5s} {'*' * int(c['importance'] or 1):3s} {c['name']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
