"""Read-only target history; executions come only from inspected paper ledgers."""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from ai_semi_intel.portfolio.constructions import REGISTRY
from ai_semi_intel.portfolio.target_archive import SCHEMA
from ai_semi_intel.trading_policy import current_status


def _read(path):
    return json.loads(path.read_text())


def _weights(snapshot):
    weights = snapshot.get("weights")
    if not isinstance(weights, dict) or not weights or "cash" not in snapshot:
        raise ValueError("missing canonical target weights or cash")
    out = {str(k): float(v) for k, v in weights.items()}
    cash = float(snapshot["cash"])
    if not all(math.isfinite(v) for v in [*out.values(), cash]):
        raise ValueError("non-finite target weights")
    if abs(sum(out.values()) + cash - 1) > 1e-6:
        raise ValueError("target weights and cash do not reconcile")
    return out, cash


def _method_view(folder, snapshot, construction, basis):
    """Read the requested method's original bytes; never rescale an old alternative after the fact."""
    method = snapshot.get("construction") if construction == "active" else construction
    view = dict(snapshot, original_construction=snapshot.get("construction"), construction=method,
                weight_basis=basis, target_source="primary_snapshot")
    if construction == "active" and basis == "target":
        return view
    path = folder / "construction_targets.json"
    if path.exists():
        document = _read(path)
        if document.get("schema_version") != SCHEMA or any(
            document.get(k) != snapshot.get(k) for k in ("run_id", "generated_at", "asof_session")
        ):
            raise ValueError("construction archive identity mismatch")
        record = document.get("constructions", {}).get(method)
        if record is None:
            return None
        if record.get("construction") != method:
            raise ValueError("construction identity mismatch")
        view.update(weights=record["model_weights" if basis == "model" else "target_weights"],
                    cash=0.0 if basis == "model" else record["cash"],
                    target_source="construction_archive", vol_target=record.get("vol_target"),
                    tracking_id=record.get("tracking_id"))
        if construction != "active":
            view["construction_comparison"] = record.get("comparisons", {}).get(basis)
        return view
    if basis == "target":
        return view if method == snapshot.get("construction") else None
    # Model weights were already calculated and archived by older pipeline runs.
    if method == snapshot.get("construction") and snapshot.get("weights_model"):
        view.update(weights=snapshot["weights_model"], cash=0.0, target_source="archived_model_weights")
        return view
    path = folder / "portfolio_weights.csv"
    if not path.exists():
        return None
    with path.open() as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        column = method if method in fields else "min_vol" if method == "min_variance" and "min_vol" in fields else None
        if column is None:
            return None
        weights = {}
        for row in reader:
            ticker = row[fields[0]]
            if ticker in weights:
                raise ValueError("duplicate ticker in construction archive")
            weights[ticker] = float(row[column])
    view.update(weights=weights, cash=0.0, target_source="archived_model_weights")
    return view


def _executions(paper):
    by_run = defaultdict(lambda: {"orders": [], "fills": []})
    if paper is None:
        return by_run, "unavailable"
    state = "incomplete" if paper.get("corrupt_ledgers") else "inspected"
    for session in paper.get("sessions", []):
        orders = {o["order_id"]: o for o in session.get("orders", [])}
        for order in orders.values():
            if order.get("strategy_id") == "stock_select":
                by_run[order["run_id"]]["orders"].append({**order, "session_id": session["session_id"]})
        for fill in session.get("fills", []):
            order = orders.get(fill.get("order_id"))
            if order is None:
                state = "incomplete"
            elif order.get("strategy_id") == "stock_select":
                by_run[order["run_id"]]["fills"].append({
                    **{k: fill.get(k) for k in ("fill_id", "order_id", "symbol", "side", "quantity", "price", "fill_time", "commission", "total_cost", "mode")},
                    "session_id": session["session_id"], "currency": order.get("currency"),
                    "checkpoint_verified": (session.get("external_checkpoint") or {}).get("synchronized") is True,
                })
    return by_run, state


def payload(history: Path, paper: dict | None = None, *, construction="active", basis="target", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if construction not in ("active", *REGISTRY) or basis not in ("target", "model"):
        raise ValueError("unknown construction or weight basis")
    root = Path(history) / "runs"
    warnings, loaded = [], {}
    try:
        index = _read(root / "index.json")
        if not isinstance(index, list) or any(not isinstance(r, dict) for r in index):
            raise ValueError("invalid index")
    except FileNotFoundError:
        index = []
    except (OSError, ValueError):
        index = []
        warnings.append("目标归档索引无法读取，请检查归档。")
    index = sorted(index, key=lambda r: (r.get("asof_session", ""), r.get("generated_at", "")))
    canonical = {}
    for row in index:
        day = row.get("asof_session")
        if row.get("status") in ("approved", "pending"):
            old = canonical.get(day)
            if old is None or row.get("status") == "approved" or old.get("status") != "approved":
                canonical[day] = row
        rid = row.get("run_id", "")
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
                raise ValueError("unsafe archive id")
            snapshot, run = _read(root / rid / "snapshot.json"), _read(root / rid / "run.json")
            if not isinstance(snapshot, dict) or not isinstance(run, dict):
                raise ValueError("archive records must be objects")
            if snapshot.get("run_id") != rid or run.get("run_id") != rid:
                raise ValueError("archive identity mismatch")
            if snapshot.get("date", snapshot.get("asof_session")) != day or run.get("asof_session") != day:
                raise ValueError("archive session mismatch")
            snapshot = _method_view(root / rid, snapshot, construction, basis)
            if snapshot is not None:
                weights, cash = _weights(snapshot)
                loaded[rid] = (snapshot, run, weights, cash)
        except (OSError, ValueError, TypeError, KeyError):
            warnings.append(f"{rid}：归档缺失或不一致，无法展示目标明细。")
    executions, ledger_state = _executions(paper)
    items = []
    for row in reversed(index):
        rid, day = row.get("run_id"), row.get("asof_session")
        current = loaded.get(rid)
        item = {"run_id": rid, "date": day, "generated_at": row.get("generated_at"),
                "canonical": canonical.get(day, {}).get("run_id") == rid,
                "version_status": row.get("status"), "supersedes": row.get("supersedes"),
                "available": current is not None, "weight_basis": basis, "validation_status": "NOT VALIDATED",
                "unavailable_reason": "此日期未归档所选方法和口径的目标，或归档不完整。旧日期可尝试查看股票内部权重。" if current is None else None}
        if current is None:
            items.append(item)
            continue
        snapshot, run, weights, cash = current
        bound = snapshot.get("construction_comparison")
        prior_id = bound.get("prior_run_id") if bound is not None else run.get("approved_prior_run_id") if construction == "active" else None
        comparison_ref = "archived_prior" if prior_id else "previous_archived_date"
        if not prior_id and bound is None:
            prior_dates = sorted(d for d in canonical if d and d < day)
            prior_id = canonical[prior_dates[-1]]["run_id"] if prior_dates else None
        prior = loaded.get(prior_id)
        # A recorded reference is never silently substituted or compared to a later/same-day run.
        if prior and prior[0].get("date", prior[0].get("asof_session")) >= day:
            prior = None
        old_weights, old_cash = (prior[2], prior[3]) if prior else ({}, None)
        rows = []
        for ticker in sorted(set(weights) | set(old_weights)):
            before, after = old_weights.get(ticker, 0), weights.get(ticker, 0)
            delta = after - before if prior else None
            action = ("initial" if prior_id is None else "unknown") if not prior else (
                "entry" if abs(before) < 1e-10 and abs(after) >= 1e-10 else
                "exit" if abs(after) < 1e-10 and abs(before) >= 1e-10 else
                "increase" if delta > 1e-10 else "decrease" if delta < -1e-10 else "unchanged")
            rows.append({"ticker": ticker, "before": before if prior else None, "after": after, "delta": delta, "action": action})
        cash_delta = cash - old_cash if prior else None
        executable_track = basis == "target" and (construction == "active" or snapshot.get("original_construction") == construction)
        activity = executions.get(rid, {"orders": [], "fills": []}) if executable_track else {"orders": [], "fills": []}
        execution_status = ("paper_fills" if activity["fills"] else "orders_only" if activity["orders"] else
                            "tracking_only" if not executable_track else "no_fills" if ledger_state == "inspected" else "unknown")
        item.update(targets=rows, cash=cash, previous_cash=old_cash, cash_delta=cash_delta,
                    prior_run_id=prior_id, prior_date=prior[0].get("date") if prior else None,
                    comparison_basis=comparison_ref if prior else "missing_prior" if prior_id else "initial",
                    target_turnover=(sum(abs(r["delta"]) for r in rows) + abs(cash_delta)) / 2 if prior else None,
                    entries=[r["ticker"] for r in rows if r["action"] == "entry"],
                    exits=[r["ticker"] for r in rows if r["action"] == "exit"],
                    construction=snapshot.get("construction"), policy=snapshot.get("rebalance_policy") or {},
                    current_target_status=current_status(run, now),
                    construction_label=REGISTRY.get(snapshot.get("construction"), {}).get("label", snapshot.get("construction") or "历史主组合"),
                    active_at_generation=executable_track, target_source=snapshot.get("target_source"),
                    previous_construction=prior[0].get("construction") if prior else None,
                    construction_changed=bool(prior and prior[0].get("construction") != snapshot.get("construction")),
                    vol_target=snapshot.get("vol_target"),
                    price_session=(run.get("data_cutoff") or {}).get("prices"),
                    input_ready=run.get("actionable"), reasons=run.get("not_actionable_reasons", []),
                    execution_status=execution_status, fills=activity["fills"], order_count=len(activity["orders"]),
                    archive_note=snapshot.get("note") or run.get("note"))
        items.append(item)
    if ledger_state != "inspected":
        warnings.append("模拟成交账本未完整读取，未找到的成交不能认定为零成交。")
    return {"items": items, "canonical_count": sum(i["canonical"] and i["available"] for i in items),
            "version_count": sum(i["available"] for i in items), "index_version_count": len(items),
            "construction": construction, "weight_basis": basis,
            "methods": [{"id": "active", "label": "主组合 · 当时选用的方法"}] + [{"id": k, "label": v["label"]} for k, v in REGISTRY.items()],
            "ledger_state": ledger_state, "warnings": warnings,
            "generated_at": now.isoformat(), "live_trading_supported": False,
            "basis": "archived targets; target turnover includes cash and is not realised trading turnover"}
