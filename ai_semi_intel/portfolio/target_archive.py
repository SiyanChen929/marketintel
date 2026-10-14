"""Per-construction daily targets. No orders, price replay or retrospective targets."""
from __future__ import annotations

import math
import csv
import json
from pathlib import Path


SCHEMA = "stock_select.construction_targets.v1"


def validate(document: dict, run: dict, snapshot: dict) -> int:
    """Validate frozen account targets without rebuilding or repairing evidence."""
    from .constructions import REGISTRY
    if not all(isinstance(value, dict) for value in (document, run, snapshot)):
        raise ValueError("target evidence objects are missing")
    if document.get("schema_version") != SCHEMA or document.get("complete") is not True:
        raise ValueError("target archive schema or completion status is invalid")
    for key in ("run_id", "generated_at", "asof_session"):
        if not run.get(key) or document.get(key) != run[key] or snapshot.get(key) != run[key]:
            raise ValueError("target archive identity mismatch: " + key)
    records = document.get("constructions")
    if not isinstance(records, dict) or set(records) != set(REGISTRY):
        raise ValueError("target archive must contain every registered construction")
    if type(document.get("construction_count")) is not int or document["construction_count"] != len(records):
        raise ValueError("target archive construction count is inconsistent")
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    for method, item in records.items():
        if not isinstance(item, dict) or item.get("construction") != method:
            raise ValueError("construction identity mismatch: " + method)
        model, target = item.get("model_weights"), item.get("target_weights")
        if (not isinstance(model, dict) or not model or not isinstance(target, dict)
                or set(model) != set(target) or any(not isinstance(t, str) or not t for t in model)):
            raise ValueError("construction security weights are missing: " + method)
        cash, scale, vol, limit = (item.get(k) for k in ("cash", "scale", "model_vol", "vol_target"))
        values = [*model.values(), *target.values(), cash, scale]
        if (not all(number(v) and 0 <= v <= 1 for v in values)
                or not number(vol) or vol < 0 or not number(limit) or limit <= 0):
            raise ValueError("construction contains invalid weights or sizing: " + method)
        if abs(sum(model.values()) - 1) > 1e-6 or abs(sum(target.values()) + cash - 1) > 1e-6:
            raise ValueError("construction weights and cash do not reconcile: " + method)
        expected = min(1., limit / vol) if vol else 1.
        if (item.get("sizing_policy") != "vol_target_no_leverage_v1"
                or abs(scale - expected) > 1e-10 or abs(cash - (1 - scale)) > 1e-10
                or any(abs(target[t] - model[t] * scale) > 1e-10 for t in model)):
            raise ValueError("construction scaling does not match its frozen policy: " + method)
    active = document.get("active_construction")
    if active not in records or snapshot.get("construction") != active:
        raise ValueError("active construction identity mismatch")
    if (records[active]["target_weights"] != snapshot.get("weights")
            or records[active]["cash"] != snapshot.get("cash")
            or document.get("rebalance_policy") != run.get("rebalance_policy")
            or snapshot.get("rebalance_policy") != run.get("rebalance_policy")):
        raise ValueError("active target differs from its canonical snapshot or clock")
    return len(records)


def bind_previous(document: dict, history: Path, prior_id: str | None) -> dict:
    """Freeze differences at generation time, including the exact previous run reference."""
    previous, old_snapshot, models = {}, {}, {}
    if prior_id:
        folder = Path(history) / "runs" / prior_id
        path = folder / "snapshot.json"
        if path.exists():
            old_snapshot = json.loads(path.read_text())
            if old_snapshot.get("run_id") != prior_id or old_snapshot.get("asof_session") >= document["asof_session"]:
                raise ValueError("invalid previous construction session")
        path = folder / "construction_targets.json"
        if path.exists():
            archive = json.loads(path.read_text())
            if archive.get("run_id") != prior_id or archive.get("schema_version") != SCHEMA:
                raise ValueError("invalid previous construction archive")
            previous = archive["constructions"]
        path = folder / "portfolio_weights.csv"
        if path.exists():
            with path.open() as stream:
                reader = csv.DictReader(stream)
                columns = reader.fieldnames or []
                rows = list(reader)
            for name in document["constructions"]:
                col = name if name in columns else "min_vol" if name == "min_variance" and "min_vol" in columns else None
                if col:
                    models[name] = {r[columns[0]]: float(r[col]) for r in rows}
    for method, current in document["constructions"].items():
        old = previous.get(method, {})
        old_target = old.get("target_weights")
        old_cash = old.get("cash")
        if not old and old_snapshot.get("construction") == method:
            old_target, old_cash = old_snapshot.get("weights"), old_snapshot.get("cash")
        old_model = old.get("model_weights", models.get(method))
        current["comparisons"] = {}
        for basis, weights, cash, prior_weights, prior_cash in (
            ("target", current["target_weights"], current["cash"], old_target, old_cash),
            ("model", current["model_weights"], 0.0, old_model, 0.0),
        ):
            known = bool(prior_weights) and prior_cash is not None
            if known and (not all(math.isfinite(float(v)) for v in [*prior_weights.values(), prior_cash])
                          or abs(sum(prior_weights.values()) + prior_cash - 1) > 1e-6):
                raise ValueError("invalid previous weights for " + method)
            actions = []
            for ticker in sorted(set(weights) | (set(prior_weights) if known else set())):
                before, after = prior_weights.get(ticker, 0.0) if known else None, weights.get(ticker, 0.0)
                change = after - before if known else None
                action = ("initial" if prior_id is None else "unknown") if not known else (
                    "entry" if abs(before) < 1e-10 and abs(after) >= 1e-10 else
                    "exit" if abs(after) < 1e-10 and abs(before) >= 1e-10 else
                    "increase" if change > 1e-10 else "decrease" if change < -1e-10 else "unchanged")
                actions.append({"ticker": ticker, "before": before, "after": after, "delta": change, "action": action})
            cash_change = cash - prior_cash if known else None
            current["comparisons"][basis] = {
                "prior_run_id": prior_id, "prior_date": old_snapshot.get("asof_session") if known else None,
                "basis": "same_construction" if known else "missing_prior" if prior_id else "initial",
                "actions": actions, "previous_cash": prior_cash if known else None, "cash_delta": cash_change,
                "target_turnover": (sum(abs(r["delta"]) for r in actions) + abs(cash_change)) / 2 if known else None,
            }
    return document


def build(opt: dict, snapshot: dict, methods: list[str] | tuple[str, ...]) -> dict:
    target_vol = float(snapshot["vol_target"])
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise ValueError("invalid target volatility")
    records = {}
    for method in methods:
        item = opt[method]
        raw = item["weights"]
        weights = {str(k): float(v) for k, v in (raw.to_dict() if hasattr(raw, "to_dict") else raw).items()}
        vol = float(item["vol"])
        if not weights or not all(math.isfinite(w) and w >= 0 for w in weights.values()):
            raise ValueError(f"invalid construction weights: {method}")
        if abs(sum(weights.values()) - 1) > 1e-6 or not math.isfinite(vol) or vol < 0:
            raise ValueError(f"unreconciled construction: {method}")
        scale = min(1.0, target_vol / vol) if vol > 0 else 1.0
        records[method] = {
            "construction": method, "label": item.get("label", method),
            "model_weights": weights, "model_vol": vol,
            "target_weights": {t: w * scale for t, w in weights.items()},
            "cash": 1 - scale, "scale": scale, "vol_target": target_vol,
            "sizing_policy": "vol_target_no_leverage_v1",
            "tracking_id": f"stock_select:{method}:vol_target_no_leverage_v1",
            "execution_status": "TARGET_ONLY", "validation_status": "NOT VALIDATED",
        }
    active = snapshot["construction"]
    if active not in records:
        raise ValueError("active construction missing from daily targets")
    current = records[active]
    if set(current["target_weights"]) != set(snapshot["weights"]) or any(
        abs(w - float(snapshot["weights"][t])) > 1e-10 for t, w in current["target_weights"].items()
    ) or abs(current["cash"] - float(snapshot["cash"])) > 1e-10:
        raise ValueError("active target disagrees with its construction archive")
    return {"schema_version": SCHEMA, "run_id": snapshot["run_id"],
            "generated_at": snapshot["generated_at"], "asof_session": snapshot["asof_session"],
            "active_construction": active, "rebalance_policy": snapshot.get("rebalance_policy"),
            "complete": True, "construction_count": len(records), "constructions": records,
            "validation_status": "NOT VALIDATED"}
