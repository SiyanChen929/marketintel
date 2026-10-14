"""Read-only operational and research evidence for the existing research desk.

An open target clock or passing software tests never grants trading permission.
The view pins its target checks to one archived run, and reports missing evidence.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from ai_semi_intel import daily
from ai_semi_intel.portfolio.constructions import REGISTRY
from ai_semi_intel.portfolio.target_archive import SCHEMA, validate
from . import config


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def construction_status(folder: Path) -> dict:
    """Check identity and accounting of the stored targets without recalculating them."""
    run, snap = _object(folder / "run.json"), _object(folder / "snapshot.json")
    document = _object(folder / "construction_targets.json")
    records = document.get("constructions")
    errors = []
    if document.get("schema_version") != SCHEMA or not isinstance(records, dict):
        return {"status": "unavailable", "count": 0, "expected": len(REGISTRY),
                "run_id": run.get("run_id"), "reasons": ["未找到完整的 construction 目标归档"]}
    try:
        validate(document, run, snap)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    active = document.get("active_construction")
    return {"status": "complete" if not errors else "inconsistent", "count": len(records),
            "expected": len(REGISTRY), "run_id": run.get("run_id"), "reasons": errors,
            "active_construction": active, "execution_status": "TARGET_ONLY"}


def strategic_status(latest: Path) -> dict:
    from ai_semi_intel.analysis import strategic
    runtime = strategic.runtime_status() if hasattr(strategic, "runtime_status") else {"available": None}
    refresh = _object(latest / "strategic_refresh.json")
    try:
        with (latest / "strategic_table.csv").open() as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, ValueError):
        rows = []
    evidence = sum(row.get("strategic_source") == "evidence_assessment" for row in rows)
    stale = sum(str(row.get("strategic_stale", "")).lower() in ("true", "1", "1.0") for row in rows)
    availability = {name: sum(row.get("strategic_availability") == name for row in rows)
                    for name in ("rejected", "legacy_unverified", "timestamped_source_unverified", "missing", "reference_prior")}
    availability["unrecorded"] = sum(not row.get("strategic_availability") for row in rows)
    return {"runtime": runtime, "refresh": {k: refresh.get(k) for k in (
        "status", "allow_llm_refresh", "client_available", "provider", "model", "dry_run", "queued")},
        "assessed_count": len(refresh.get("assessed") or []) if refresh else None,
        "refresh_evidence": "recorded" if refresh else "unavailable", "universe_count": len(rows),
        "evidence_assessment_count": evidence, "stale_count": stale, "availability": availability,
        "scope": "保存的评估覆盖率；不代表今天已重新调用 LLM，也不证明评分有超额收益"}


def shadow_status(outputs: Path) -> dict:
    from .platform.shadow_ledger import inspect_shadow_artifact
    # Only this explicitly designated production artifact is inspected. Review
    # fixtures and test outputs must never appear as an account track record.
    report = inspect_shadow_artifact(outputs / "shadow" / "ledger.json")
    return {**report, "engine_status": "implemented", "live_trading": False,
            "deployment_status": "BLOCKED", "performance_validated": False}


def candidate_status(outputs: Path, run_id: str, now: datetime) -> dict:
    from ai_semi_intel.research.challengers import inspect_registration
    if not run_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) or run_id in (".", ".."):
        return {"status": "unavailable", "items": []}
    root = outputs / "history" / "research_candidates"
    items = [inspect_registration(path, now=now, current_run_id=run_id)
             for path in sorted(root.glob("*/" + run_id + ".json"))]
    if items:
        return {"status": "recorded", "items": items}
    diagnostic = _object(outputs / "latest" / "research_candidates.json")
    if diagnostic.get("run_id") == run_id and diagnostic.get("status") == "error":
        try:
            at = datetime.fromisoformat(diagnostic["evaluated_at"])
            if at.tzinfo is not None and at <= now:
                return {"status": "error", "items": [], "reason": "本次评分改进对照未能登记"}
        except (ValueError, TypeError, KeyError):
            pass
    return {"status": "not_started", "items": []}


def payload(outputs: Path | None = None, now: datetime | None = None) -> dict:
    outputs, now = Path(outputs or config.OUTPUTS), now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("readiness clock must be timezone-aware")
    latest = outputs / "latest"
    current = _object(latest / "run.json")
    rid = current.get("run_id", "")
    folder = outputs / "history" / "runs" / rid if isinstance(rid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", rid) else latest
    pinned = folder != latest and (folder / "run.json").is_file()
    if not pinned:
        folder = latest
    if outputs == config.OUTPUTS:
        folder = config.run_folder()
        pinned = folder != latest
        rid = _object(folder / "run.json").get("run_id", "")
    operation = daily.status(now, state_dir=outputs / "daily", history=outputs / "history", latest=folder)
    # The target is frozen, but a later failed attempt remains a current gate.
    current_gate = config.run_info(now, latest=folder, blocked_dir=latest)
    operation.update(current_target_status=current_gate["current_target_status"],
                     actionable_now=current_gate["actionable_now"], blocked=current_gate.get("blocked"))
    targets = construction_status(folder)
    strategic = strategic_status(folder)
    shadow = shadow_status(outputs)
    candidates = candidate_status(outputs, rid, now)
    try:
        from ai_semi_intel.research.forward_registry import summary
        forward = summary(outputs / "history" / "forward_scores", now=now)
    except ImportError:
        forward = {"status": "unavailable", "n_records": 0, "reason": "前向评分登记尚未接入"}
    except (OSError, ValueError, TypeError):
        forward = {"status": "invalid", "n_records": None, "reason": "前向评分登记无法完整读取"}
    evaluation = _object(latest / "forward_evaluation.json")
    forward["last_evaluation"] = evaluation_status(evaluation, rid, forward, now)
    clock = operation["current_target_status"]
    missed, failed = operation.get("missed_sessions", []), operation.get("failed_sessions", [])
    incomplete = operation.get("incomplete_sessions", [])
    coverage_status = "unknown" if not operation.get("coverage_start") else "gap" if missed or failed or incomplete else "recorded"
    checks = [
        {"id": "target_clock", "title": "当前目标", "status": clock["status"],
         "detail": "；".join(clock.get("reasons", [])) or "目标时窗开放；下单仍须通过报价、账户和风险门槛", "href": "/rebalances"},
        {"id": "daily_coverage", "title": "日频记录", "status": coverage_status,
         "detail": f"{len(missed)} 个交易日未留下日频准备记录，{len(failed)} 个失败日，{len(incomplete)} 个记录不完整日；缺失历史不回填", "href": "/rebalances"},
        {"id": "heartbeat", "title": "后台任务", "status": operation.get("heartbeat_status", "unknown"),
         "detail": "按最近轮询心跳判断；电脑休眠可能错过准备窗口", "href": "#pipe-panel"},
        {"id": "constructions", "title": "方法独立归档", "status": targets["status"],
         "detail": f"{targets['count']} / {targets['expected']} 种方法；" + ("；".join(targets["reasons"]) or "目标、现金和版本身份核对通过"), "href": "/rebalances"},
        {"id": "forward", "title": "主观评分的前向证据", "status": "NOT VALIDATED",
         "detail": "按真正记录时间积累预测和成熟标签；数值、固定规则、LLM 三组分别追踪", "href": "#forward-panel"},
        {"id": "execution", "title": "连续执行与收益", "status": "blocked",
         "detail": "当前自动任务产生目标；PAPER 隔夜公司行动与账务门槛仍保留，未启用 LIVE", "href": "/platform#paper-panel"},
        {"id": "shadow", "title": "独立影子账务", "status": shadow["status"],
         "detail": ("账务模块已实现；真实连续账本尚未开始" if shadow["status"] == "UNAVAILABLE" else
                    "仅检查本地账本重放；成交、报价及公司行动来源仍须独立核验"), "href": "/platform#paper-panel"},
    ]
    return {"schema": "stock_select.readiness.v1", "checked_at": now.astimezone(timezone.utc).isoformat(),
            "status": "NOT_DEPLOYABLE", "validation_status": "NOT VALIDATED", "run_id": rid or None,
            "pinned_archive": pinned, "operation": operation, "constructions": targets,
            "strategic": strategic, "forward": forward, "candidates": candidates, "shadow": shadow, "checks": checks,
            "live_trading": False, "scope": "运行和证据状态；工程通过不能替代投资收益验证"}


def evaluation_status(evaluation: dict, run_id: str, forward: dict, now: datetime) -> dict:
    """Only attach diagnostics bound to the displayed run and registry revision."""
    if not evaluation:
        return {"status": "unavailable", "reason": "本次运行未保存前向评价"}
    if not run_id or evaluation.get("run_id") != run_id:
        return {"status": "unavailable", "reason": "前向评价不属于当前运行"}
    try:
        at = datetime.fromisoformat(evaluation["evaluated_at"])
        if at.tzinfo is None or at > now:
            raise ValueError("invalid evaluation clock")
    except (ValueError, TypeError, KeyError):
        return {"status": "invalid", "reason": "前向评价时间无法核验"}
    keys = ("status", "run_id", "evaluated_at", "reason", "n_registered_versions")
    if evaluation.get("status") == "error":
        return {k: evaluation.get(k) for k in keys}
    if (forward.get("latest_run_id") != run_id or not forward.get("latest_record_hash")
            or evaluation.get("registration_record_hash") != forward.get("latest_record_hash")):
        return {"status": "unavailable", "reason": "前向评价的登记版本无法核验"}
    return {k: evaluation.get(k) for k in keys}
