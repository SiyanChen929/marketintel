from __future__ import annotations

import json
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from market_intel.platform.checkpoint import (
    GENESIS_CHECKPOINT_HASH,
    FileLedgerCheckpointStore,
    HmacSha256Signer,
    LedgerCheckpointError,
    checkpoint_store_from_environment,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
SESSION = "paper_0123456789abcdef0123456789abcdef"


def store(tmp_path, *, secret=b"a" * 32, key_id="paper-witness-v1"):
    return FileLedgerCheckpointStore(
        tmp_path / "external-witness",
        HmacSha256Signer(key_id, secret),
    )


def publish(checkpoints, sequence, event_hash, at, previous=None):
    return checkpoints.publish(
        session_id=SESSION,
        sequence=sequence,
        event_hash=event_hash,
        timestamp=at,
        expected_previous_checkpoint_hash=previous,
    )


def test_signed_checkpoint_chain_is_monotonic_persistent_and_secret_free(tmp_path):
    checkpoints = store(tmp_path)
    first = publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    second = publish(
        checkpoints,
        7,
        "2" * 64,
        NOW + timedelta(minutes=1),
        first.checkpoint_hash,
    )

    assert second.previous_checkpoint_hash == first.checkpoint_hash
    assert checkpoints.verify_head(SESSION, 7, "2" * 64) == second
    reopened = store(tmp_path)
    assert reopened.latest(SESSION) == second
    raw = (tmp_path / "external-witness" / f"{SESSION}.checkpoints.jsonl").read_text()
    assert (b"a" * 32).hex() not in raw
    assert len(raw.splitlines()) == 2


def test_exact_publish_retry_is_idempotent_and_cas_is_enforced(tmp_path):
    checkpoints = store(tmp_path)
    first = publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    lost_ack_retry = publish(
        checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH
    )
    assert lost_ack_retry == first
    retry = publish(checkpoints, 1, "1" * 64, NOW, first.checkpoint_hash)
    assert retry == first
    with pytest.raises(LedgerCheckpointError, match="compare-and-swap"):
        publish(checkpoints, 2, "2" * 64, NOW, GENESIS_CHECKPOINT_HASH)


def test_exact_advanced_head_retry_accepts_the_original_stale_cas_token(tmp_path):
    checkpoints = store(tmp_path)
    first = publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    advanced = publish(
        checkpoints,
        4,
        "4" * 64,
        NOW + timedelta(minutes=1),
        first.checkpoint_hash,
    )

    retry = publish(
        checkpoints,
        4,
        "4" * 64,
        NOW + timedelta(minutes=1),
        first.checkpoint_hash,
    )

    assert retry == advanced
    assert len(
        (
            tmp_path
            / "external-witness"
            / f"{SESSION}.checkpoints.jsonl"
        ).read_text().splitlines()
    ) == 2


def test_concurrent_exact_publish_is_one_idempotent_checkpoint(tmp_path):
    first_store = store(tmp_path)
    second_store = store(tmp_path)
    first = publish(first_store, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)

    def advance(checkpoints):
        return publish(
            checkpoints,
            4,
            "4" * 64,
            NOW + timedelta(minutes=1),
            first.checkpoint_hash,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, (first_store, second_store)))

    assert results[0] == results[1]
    assert first_store.latest(SESSION) == results[0]
    assert len(
        (
            tmp_path
            / "external-witness"
            / f"{SESSION}.checkpoints.jsonl"
        ).read_text().splitlines()
    ) == 2


@pytest.mark.parametrize(
    ("sequence", "event_hash", "timestamp", "message"),
    [
        (0, "2" * 64, NOW + timedelta(minutes=1), "positive integer"),
        (1, "2" * 64, NOW + timedelta(minutes=1), "rollback or equivocation"),
        (2, "2" * 64, NOW - timedelta(minutes=1), "timestamp moved backward"),
    ],
)
def test_rollback_equivocation_and_backward_time_are_rejected(
    tmp_path, sequence, event_hash, timestamp, message
):
    checkpoints = store(tmp_path)
    first = publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    with pytest.raises(LedgerCheckpointError, match=message):
        publish(checkpoints, sequence, event_hash, timestamp, first.checkpoint_hash)


def test_tamper_and_key_mismatch_are_detected(tmp_path):
    checkpoints = store(tmp_path)
    publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    path = tmp_path / "external-witness" / f"{SESSION}.checkpoints.jsonl"
    row = json.loads(path.read_text())
    row["event_hash"] = "f" * 64
    path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerCheckpointError, match="signature verification"):
        checkpoints.latest(SESSION)

    clean_root = tmp_path / "other"
    original = FileLedgerCheckpointStore(clean_root, HmacSha256Signer("key-a", b"a" * 32))
    publish(original, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    wrong_key = FileLedgerCheckpointStore(clean_root, HmacSha256Signer("key-b", b"b" * 32))
    with pytest.raises(LedgerCheckpointError, match="signature verification"):
        wrong_key.latest(SESSION)


def test_verify_head_rejects_missing_or_different_ledger_head(tmp_path):
    checkpoints = store(tmp_path)
    with pytest.raises(LedgerCheckpointError, match="no external"):
        checkpoints.verify_head(SESSION, 1, "1" * 64)
    publish(checkpoints, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH)
    with pytest.raises(LedgerCheckpointError, match="does not match"):
        checkpoints.verify_head(SESSION, 2, "2" * 64)


def test_environment_checkpoint_adapter_is_opt_in_and_requires_complete_config(tmp_path):
    assert checkpoint_store_from_environment({}) is None
    with pytest.raises(LedgerCheckpointError, match="requires DIR, KEY_ID and HMAC_KEY_B64"):
        checkpoint_store_from_environment(
            {"MARKET_INTEL_PAPER_CHECKPOINT_DIR": str(tmp_path / "witness")}
        )
    configured = checkpoint_store_from_environment(
        {
            "MARKET_INTEL_PAPER_CHECKPOINT_DIR": str(tmp_path / "witness"),
            "MARKET_INTEL_PAPER_CHECKPOINT_KEY_ID": "env-key-v1",
            "MARKET_INTEL_PAPER_CHECKPOINT_HMAC_KEY_B64": base64.b64encode(
                b"k" * 32
            ).decode(),
        }
    )
    assert configured is not None
    checkpoint = publish(
        configured, 1, "1" * 64, NOW, GENESIS_CHECKPOINT_HASH
    )
    assert checkpoint.key_id == "env-key-v1"
