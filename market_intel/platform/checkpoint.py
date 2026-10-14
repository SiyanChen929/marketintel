"""Signed, monotonic witnesses for PAPER ledger heads.

The JSONL ledger and its local ``.head`` file deliberately remain a single
crash-recovery unit.  This module defines the separate trust-domain boundary:
an operator may point :class:`FileLedgerCheckpointStore` at a protected mount,
or implement :class:`LedgerCheckpointStore` for a remote/WORM service.

The bundled file store is a reference adapter, not a claim that a directory on
the same machine is independent.  Authenticity comes from a runtime-injected
key; the key is never written to the checkpoint log.
"""

from __future__ import annotations

import fcntl
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable


CHECKPOINT_SCHEMA_VERSION = "platform.paper.ledger-checkpoint.v1"
GENESIS_CHECKPOINT_HASH = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class LedgerCheckpointError(RuntimeError):
    """A signed checkpoint cannot be trusted or advanced safely."""


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LedgerCheckpointError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@runtime_checkable
class CheckpointSigner(Protocol):
    """Runtime key boundary.  Implementations must not persist key material."""

    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes) -> str: ...

    def verify(self, payload: bytes, signature: str, key_id: str) -> bool: ...


class HmacSha256Signer:
    """Small reference signer for tests and protected single-host deployments."""

    def __init__(self, key_id: str, secret: bytes | bytearray | memoryview) -> None:
        identity = str(key_id).strip()
        key = bytes(secret)
        if not _KEY_RE.fullmatch(identity):
            raise LedgerCheckpointError("checkpoint key_id is invalid")
        if len(key) < 32:
            raise LedgerCheckpointError("checkpoint HMAC key must contain at least 32 bytes")
        self._key_id = identity
        self._key = key

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, bytes(payload), hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str, key_id: str) -> bool:
        if key_id != self._key_id or not isinstance(signature, str):
            return False
        expected = self.sign(payload)
        return hmac.compare_digest(expected, signature.lower())


@dataclass(frozen=True, slots=True)
class LedgerCheckpoint:
    session_id: str
    sequence: int
    event_hash: str
    timestamp: datetime
    previous_checkpoint_hash: str
    key_id: str
    signature: str
    schema_version: str = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        session = str(self.session_id).strip()
        key_id = str(self.key_id).strip()
        event_hash = str(self.event_hash).strip().lower()
        previous = str(self.previous_checkpoint_hash).strip().lower()
        signature = str(self.signature).strip().lower()
        if not _SESSION_RE.fullmatch(session):
            raise LedgerCheckpointError("checkpoint session_id is invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise LedgerCheckpointError("checkpoint sequence must be a positive integer")
        if not _SHA256_RE.fullmatch(event_hash):
            raise LedgerCheckpointError("checkpoint event_hash must be a full SHA-256")
        if not _SHA256_RE.fullmatch(previous):
            raise LedgerCheckpointError(
                "checkpoint previous_checkpoint_hash must be a full SHA-256"
            )
        if not _KEY_RE.fullmatch(key_id):
            raise LedgerCheckpointError("checkpoint key_id is invalid")
        if not _SHA256_RE.fullmatch(signature):
            raise LedgerCheckpointError("checkpoint signature must be a SHA-256 HMAC")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise LedgerCheckpointError(
                f"checkpoint schema_version must equal {CHECKPOINT_SCHEMA_VERSION!r}"
            )
        object.__setattr__(self, "session_id", session)
        object.__setattr__(self, "event_hash", event_hash)
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "checkpoint timestamp"))
        object.__setattr__(self, "previous_checkpoint_hash", previous)
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "signature", signature)

    def signed_body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "event_hash": self.event_hash,
            "timestamp": self.timestamp.isoformat(),
            "previous_checkpoint_hash": self.previous_checkpoint_hash,
            "key_id": self.key_id,
        }

    @property
    def checkpoint_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.signed_body(), "signature": self.signature}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LedgerCheckpoint":
        if not isinstance(value, Mapping):
            raise LedgerCheckpointError("checkpoint record must be an object")
        try:
            timestamp = datetime.fromisoformat(str(value["timestamp"]))
            sequence = value["sequence"]
            return cls(
                schema_version=str(value["schema_version"]),
                session_id=str(value["session_id"]),
                sequence=sequence,
                event_hash=str(value["event_hash"]),
                timestamp=timestamp,
                previous_checkpoint_hash=str(value["previous_checkpoint_hash"]),
                key_id=str(value["key_id"]),
                signature=str(value["signature"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerCheckpointError("checkpoint record is malformed") from exc


def sign_checkpoint(
    *,
    session_id: str,
    sequence: int,
    event_hash: str,
    timestamp: datetime,
    previous_checkpoint_hash: str,
    signer: CheckpointSigner,
) -> LedgerCheckpoint:
    instant = _aware(timestamp, "checkpoint timestamp")
    body = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "session_id": str(session_id).strip(),
        "sequence": sequence,
        "event_hash": str(event_hash).strip().lower(),
        "timestamp": instant.isoformat(),
        "previous_checkpoint_hash": str(previous_checkpoint_hash).strip().lower(),
        "key_id": signer.key_id,
    }
    signature = signer.sign(_canonical(body))
    return LedgerCheckpoint(
        schema_version=body["schema_version"],
        session_id=body["session_id"],
        sequence=sequence,
        event_hash=body["event_hash"],
        timestamp=instant,
        previous_checkpoint_hash=body["previous_checkpoint_hash"],
        key_id=body["key_id"],
        signature=signature,
    )


@runtime_checkable
class LedgerCheckpointStore(Protocol):
    """Trust-domain adapter; production implementations may use WORM/remote storage."""

    def latest(self, session_id: str) -> LedgerCheckpoint | None: ...

    def publish(
        self,
        *,
        session_id: str,
        sequence: int,
        event_hash: str,
        timestamp: datetime,
        expected_previous_checkpoint_hash: str | None,
    ) -> LedgerCheckpoint: ...

    def verify_head(
        self, session_id: str, sequence: int, event_hash: str
    ) -> LedgerCheckpoint: ...


class FileLedgerCheckpointStore:
    """Append-only signed checkpoint reference adapter.

    Put ``root`` on a separately administered append-only/WORM mount to obtain
    an actual independent witness.  A normal local directory proves the API and
    signatures, but remains a shared operational failure domain.
    """

    def __init__(self, root: str | Path, signer: CheckpointSigner) -> None:
        if not isinstance(signer, CheckpointSigner):
            raise LedgerCheckpointError("checkpoint signer does not implement the required contract")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise LedgerCheckpointError("checkpoint root must be a non-symlink directory")
        self.signer = signer

    def _path(self, session_id: str) -> Path:
        session = str(session_id).strip()
        if not _SESSION_RE.fullmatch(session):
            raise LedgerCheckpointError("checkpoint session_id is invalid")
        return self.root / f"{session}.checkpoints.jsonl"

    @contextmanager
    def _locked(self, session_id: str, *, exclusive: bool) -> Iterator[Path]:
        path = self._path(session_id)
        lock = path.with_name(path.name + ".lock")
        if lock.is_symlink() or path.is_symlink():
            raise LedgerCheckpointError("checkpoint path cannot be a symlink")
        with lock.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield path
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self, path: Path, session_id: str) -> list[LedgerCheckpoint]:
        if not path.exists():
            return []
        if not path.is_file() or path.is_symlink():
            raise LedgerCheckpointError("checkpoint log is not a safe regular file")
        records: list[LedgerCheckpoint] = []
        prior_hash = GENESIS_CHECKPOINT_HASH
        prior_sequence = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    raise LedgerCheckpointError(
                        f"blank checkpoint record on line {line_number}"
                    )
                try:
                    parsed = json.loads(raw)
                    checkpoint = LedgerCheckpoint.from_dict(parsed)
                except (json.JSONDecodeError, LedgerCheckpointError) as exc:
                    raise LedgerCheckpointError(
                        f"invalid checkpoint record on line {line_number}"
                    ) from exc
                if checkpoint.session_id != session_id:
                    raise LedgerCheckpointError("checkpoint log contains another session")
                if checkpoint.sequence <= prior_sequence:
                    raise LedgerCheckpointError("checkpoint sequence is not monotonic")
                if checkpoint.previous_checkpoint_hash != prior_hash:
                    raise LedgerCheckpointError("checkpoint hash chain is broken")
                if not self.signer.verify(
                    _canonical(checkpoint.signed_body()),
                    checkpoint.signature,
                    checkpoint.key_id,
                ):
                    raise LedgerCheckpointError("checkpoint signature verification failed")
                records.append(checkpoint)
                prior_hash = checkpoint.checkpoint_hash
                prior_sequence = checkpoint.sequence
        return records

    def latest(self, session_id: str) -> LedgerCheckpoint | None:
        with self._locked(session_id, exclusive=False) as path:
            records = self._read(path, session_id)
        return records[-1] if records else None

    def publish(
        self,
        *,
        session_id: str,
        sequence: int,
        event_hash: str,
        timestamp: datetime,
        expected_previous_checkpoint_hash: str | None,
    ) -> LedgerCheckpoint:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise LedgerCheckpointError("checkpoint sequence must be a positive integer")
        event_hash = str(event_hash).strip().lower()
        if not _SHA256_RE.fullmatch(event_hash):
            raise LedgerCheckpointError("checkpoint event_hash must be a full SHA-256")
        timestamp = _aware(timestamp, "checkpoint timestamp")
        with self._locked(session_id, exclusive=True) as path:
            records = self._read(path, session_id)
            previous = records[-1] if records else None
            previous_hash = (
                previous.checkpoint_hash if previous else GENESIS_CHECKPOINT_HASH
            )
            # A caller may have lost the acknowledgement for a successful
            # append, or another repairer may have published the same head
            # after this caller read ``latest``.  Reaching the exact requested
            # state is idempotent even when the caller's CAS token is now
            # stale; no mutation or rollback is performed in this branch.
            if (
                previous is not None
                and sequence == previous.sequence
                and event_hash == previous.event_hash
            ):
                return previous
            expected = expected_previous_checkpoint_hash
            if expected is not None and str(expected).lower() != previous_hash:
                raise LedgerCheckpointError("checkpoint compare-and-swap precondition failed")
            if previous is not None:
                if sequence <= previous.sequence:
                    raise LedgerCheckpointError("checkpoint rollback or equivocation was rejected")
                if timestamp < previous.timestamp:
                    raise LedgerCheckpointError("checkpoint timestamp moved backward")
            checkpoint = sign_checkpoint(
                session_id=session_id,
                sequence=sequence,
                event_hash=event_hash,
                timestamp=timestamp,
                previous_checkpoint_hash=previous_hash,
                signer=self.signer,
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            fd = os.open(path, flags, 0o600)
            try:
                payload = _canonical(checkpoint.to_dict()) + b"\n"
                written = 0
                while written < len(payload):
                    count = os.write(fd, payload[written:])
                    if count <= 0:
                        raise LedgerCheckpointError("checkpoint append made no progress")
                    written += count
                os.fsync(fd)
            finally:
                os.close(fd)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            verified = self._read(path, session_id)
            if not verified or verified[-1] != checkpoint:
                raise LedgerCheckpointError("published checkpoint failed read-back verification")
            return checkpoint

    def verify_head(
        self, session_id: str, sequence: int, event_hash: str
    ) -> LedgerCheckpoint:
        checkpoint = self.latest(session_id)
        if checkpoint is None:
            raise LedgerCheckpointError("no external ledger checkpoint exists")
        if checkpoint.sequence != sequence or checkpoint.event_hash != str(event_hash).lower():
            raise LedgerCheckpointError("external checkpoint does not match the ledger head")
        return checkpoint


def checkpoint_store_from_environment(
    environ: Mapping[str, str] | None = None,
) -> FileLedgerCheckpointStore | None:
    """Build the opt-in file witness without ever persisting its HMAC key."""

    values = os.environ if environ is None else environ
    root = str(values.get("MARKET_INTEL_PAPER_CHECKPOINT_DIR") or "").strip()
    key_id = str(values.get("MARKET_INTEL_PAPER_CHECKPOINT_KEY_ID") or "").strip()
    encoded = str(
        values.get("MARKET_INTEL_PAPER_CHECKPOINT_HMAC_KEY_B64") or ""
    ).strip()
    if not any((root, key_id, encoded)):
        return None
    if not all((root, key_id, encoded)):
        raise LedgerCheckpointError(
            "paper checkpoint configuration requires DIR, KEY_ID and HMAC_KEY_B64"
        )
    try:
        secret = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LedgerCheckpointError(
            "MARKET_INTEL_PAPER_CHECKPOINT_HMAC_KEY_B64 is not valid base64"
        ) from exc
    return FileLedgerCheckpointStore(root, HmacSha256Signer(key_id, secret))


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "GENESIS_CHECKPOINT_HASH",
    "CheckpointSigner",
    "FileLedgerCheckpointStore",
    "HmacSha256Signer",
    "LedgerCheckpoint",
    "LedgerCheckpointError",
    "LedgerCheckpointStore",
    "checkpoint_store_from_environment",
    "sign_checkpoint",
]
