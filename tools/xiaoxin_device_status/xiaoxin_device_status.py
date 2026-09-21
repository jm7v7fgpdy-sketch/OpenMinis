#!/usr/bin/env python3
"""Map existing Open Minis apple-device / apple-healthkit JSON into XIAOXIN_DEVICE_STATUS_V1.

Read-only. Does not call HealthKit write APIs, does not control the phone, and
does not start a scheduler. Intended for a single awake Open Minis turn:

  apple-device info --compact > /tmp/xx-phone.json
  apple-healthkit steps --today --compact > /tmp/xx-steps.json
  apple-healthkit heart-rate --days 1 --limit 1 --compact > /tmp/xx-hr.json
  python3 xiaoxin_device_status.py --phone /tmp/xx-phone.json \\
      --steps /tmp/xx-steps.json --hr /tmp/xx-hr.json

Mac fields are filled by 领芯 outside this repo; pass --mac or leave the stub.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

FRESH_MAX_AGE_SECONDS = 300
HR_RECENT_MAX_SECONDS = 15 * 60
PRODUCER = "xiaoxin+lingxin"
SCHEMA_TYPE = "XIAOXIN_DEVICE_STATUS_V1"
SCHEMA_VERSION = 1

REASON_CODES = frozenset(
    {
        "PERMISSION_DENIED",
        "SOURCE_UNREACHABLE",
        "NO_RECENT_SAMPLE",
        "QUERY_FAILED",
        "TIMEOUT",
        "NOT_CONFIGURED",
    }
)

_AUTH_CODES = frozenset({"authorization_denied", "authorization_not_determined"})
_TIMEOUT_CODES = frozenset({"query_timeout", "timeout"})
_UNREACHABLE_CODES = frozenset({"not_available", "source_unreachable"})


def canonical_dumps(obj: Any) -> str:
    """RFC8785/JCS-shaped canonical JSON for this schema (ASCII keys, no NaN)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def integrity_sha256(report: Mapping[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "integrity"}
    digest = hashlib.sha256(canonical_dumps(body).encode("utf-8")).hexdigest()
    return digest.lower()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    text = dt.isoformat(timespec="seconds")
    # Python may emit "+08:00"; keep offset form required by the schema.
    return text


def _age_seconds(observed: Optional[datetime], now: datetime) -> int:
    if observed is None:
        return 0
    delta = (now - observed).total_seconds()
    if delta < 0:
        return 0
    return int(delta)


def _map_bridge_error(code: Optional[str]) -> str:
    if not code:
        return "QUERY_FAILED"
    lowered = str(code).lower()
    if lowered in _AUTH_CODES:
        return "PERMISSION_DENIED"
    if lowered in _TIMEOUT_CODES:
        return "TIMEOUT"
    if lowered in _UNREACHABLE_CODES:
        return "SOURCE_UNREACHABLE"
    return "QUERY_FAILED"


def _error_code(payload: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not payload or not isinstance(payload, Mapping):
        return None
    if payload.get("ok") is False:
        err = payload.get("error") or {}
        if isinstance(err, Mapping):
            return err.get("code")
        if isinstance(err, str):
            return err
    return None


def _is_ok(payload: Optional[Mapping[str, Any]]) -> bool:
    return bool(isinstance(payload, Mapping) and payload.get("ok") is True)


def _charging(state: Any) -> str:
    if state in ("charging", "full"):
        return "yes"
    if state == "unplugged":
        return "no"
    return "unknown"


def _mac_stub() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "observed_at": None,
        "age_seconds": 0,
        "hostname": None,
        "uptime_seconds": 0,
        "load_1m": 0,
        "disk_free_pct": 0,
        "reason_code": "NOT_CONFIGURED",
    }


def _phone_block(phone: Optional[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    empty = {
        "status": "unavailable",
        "observed_at": None,
        "age_seconds": 0,
        "model": None,
        "os_version": None,
        "battery_pct": 0,
        "charging": "unknown",
        "reason_code": "SOURCE_UNREACHABLE",
    }
    if phone is None:
        return empty
    if not isinstance(phone, Mapping):
        empty["reason_code"] = "QUERY_FAILED"
        return empty
    if not _is_ok(phone):
        empty["reason_code"] = _map_bridge_error(_error_code(phone))
        ts = _parse_dt(phone.get("timestamp") if isinstance(phone, Mapping) else None)
        if ts is not None:
            empty["observed_at"] = _rfc3339(ts)
            empty["age_seconds"] = _age_seconds(ts, now)
        return empty

    data = phone.get("data") or {}
    device = data.get("device") or {}
    battery = data.get("battery") or {}
    observed = _parse_dt(phone.get("timestamp"))
    age = _age_seconds(observed, now)
    level = battery.get("level_percent")
    if not isinstance(level, int):
        try:
            level = int(level)
        except (TypeError, ValueError):
            raw = battery.get("level")
            try:
                level = int(float(raw) * 100) if raw is not None else 0
            except (TypeError, ValueError):
                level = 0
    model = data.get("machine") or device.get("model")
    os_version = device.get("system_version")
    if isinstance(model, str) and not model.strip():
        model = None
    if isinstance(os_version, str) and not os_version.strip():
        os_version = None
    status = "fresh" if observed is not None and age <= FRESH_MAX_AGE_SECONDS else (
        "stale" if observed is not None else "unavailable"
    )
    reason = None
    if status == "unavailable":
        reason = "QUERY_FAILED"
    return {
        "status": status,
        "observed_at": _rfc3339(observed) if observed else None,
        "age_seconds": age,
        "model": model,
        "os_version": os_version,
        "battery_pct": level,
        "charging": _charging(battery.get("state")),
        "reason_code": reason,
    }


def _steps_value(steps: Optional[Mapping[str, Any]]) -> tuple[int, Optional[str], Optional[datetime]]:
    if not _is_ok(steps):
        return 0, _error_code(steps), _parse_dt((steps or {}).get("timestamp") if steps else None)
    data = (steps or {}).get("data") or {}
    total = data.get("total")
    try:
        value = int(total)
    except (TypeError, ValueError):
        value = 0
    return value, None, _parse_dt(steps.get("timestamp"))


def _hr_value(
    heart_rate: Optional[Mapping[str, Any]], now: datetime
) -> tuple[int, Optional[str], Optional[str], Optional[str], Optional[datetime]]:
    """Returns bpm, sample_at, error_code, no_recent_reason, queried_at."""
    if not _is_ok(heart_rate):
        return 0, None, _error_code(heart_rate), None, _parse_dt(
            (heart_rate or {}).get("timestamp") if heart_rate else None
        )
    queried = _parse_dt(heart_rate.get("timestamp"))
    data = heart_rate.get("data") or {}
    samples = data.get("samples") or []
    if not samples:
        return 0, None, None, "NO_RECENT_SAMPLE", queried
    first = samples[0] if isinstance(samples[0], Mapping) else {}
    bpm = first.get("bpm", first.get("value"))
    try:
        value = int(bpm)
    except (TypeError, ValueError):
        value = 0
    sample_at = first.get("date") or first.get("observed_at")
    sample_dt = _parse_dt(sample_at if isinstance(sample_at, str) else None)
    if sample_dt is None:
        return value, None, None, "NO_RECENT_SAMPLE", queried
    age = _age_seconds(sample_dt, now)
    reason = "NO_RECENT_SAMPLE" if age > HR_RECENT_MAX_SECONDS else None
    return value, _rfc3339(sample_dt), None, reason, queried


def _health_block(
    steps: Optional[Mapping[str, Any]],
    heart_rate: Optional[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    steps_val, steps_err, steps_ts = _steps_value(steps)
    hr_val, hr_at, hr_err, hr_recent, hr_ts = _hr_value(heart_rate, now)

    errors = [c for c in (steps_err, hr_err) if c]
    mapped = [_map_bridge_error(c) for c in errors]
    queried_candidates = [ts for ts in (steps_ts, hr_ts) if ts is not None]
    queried_at = max(queried_candidates) if queried_candidates else None

    def pack(status: str, reason: Optional[str]) -> dict[str, Any]:
        return {
            "status": status,
            "queried_at": _rfc3339(queried_at) if queried_at else None,
            "steps_today": {"value": steps_val, "source_last_sample_at": None},
            "latest_heart_rate_bpm": {"value": hr_val, "observed_at": hr_at},
            "reason_code": reason,
        }

    if "TIMEOUT" in mapped:
        return pack("unavailable", "TIMEOUT")
    if mapped and all(m == "PERMISSION_DENIED" for m in mapped) and not _is_ok(steps) and not _is_ok(heart_rate):
        return pack("unavailable", "PERMISSION_DENIED")
    if not _is_ok(steps) and not _is_ok(heart_rate):
        reason = mapped[0] if mapped else "SOURCE_UNREACHABLE"
        return pack("unavailable", reason)

    query_age = _age_seconds(queried_at, now) if queried_at else None
    if queried_at is None:
        status = "unavailable"
        reason = "QUERY_FAILED"
    elif query_age <= FRESH_MAX_AGE_SECONDS:
        status = "fresh"
        reason = hr_recent
    else:
        status = "stale"
        reason = hr_recent
    return pack(status, reason)


def _normalize_mac(mac: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not mac:
        return _mac_stub()
    stub = _mac_stub()
    out = dict(stub)
    for key in stub:
        if key in mac:
            out[key] = mac[key]
    if out.get("reason_code") not in REASON_CODES and out.get("reason_code") is not None:
        out["reason_code"] = "QUERY_FAILED"
    return out


def build_report(
    *,
    phone: Optional[Mapping[str, Any]] = None,
    steps: Optional[Mapping[str, Any]] = None,
    heart_rate: Optional[Mapping[str, Any]] = None,
    mac: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
    report_id: Optional[str] = None,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    report = {
        "type": SCHEMA_TYPE,
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id or str(uuid.uuid4()),
        "generated_at": _rfc3339(now),
        "producer": PRODUCER,
        "phone": _phone_block(phone, now),
        "health": _health_block(steps, heart_rate, now),
        "mac": _normalize_mac(mac),
    }
    report["integrity"] = {"canonical_sha256": integrity_sha256(report)}
    return report


def _load_json(path: Optional[str]) -> Optional[Any]:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble XIAOXIN_DEVICE_STATUS_V1 from apple-device / apple-healthkit JSON."
    )
    parser.add_argument("--phone", help="JSON from `apple-device info --compact`")
    parser.add_argument("--steps", help="JSON from `apple-healthkit steps --today --compact`")
    parser.add_argument("--hr", help="JSON from `apple-healthkit heart-rate --days 1 --limit 1 --compact`")
    parser.add_argument("--mac", help="Optional mac block JSON from 领芯 (not this repo)")
    parser.add_argument("--report-id", help="Override report_id (tests); default uuid4")
    args = parser.parse_args(argv)

    phone = _load_json(args.phone)
    steps = _load_json(args.steps)
    hr = _load_json(args.hr)
    mac = _load_json(args.mac)
    report = build_report(
        phone=phone, steps=steps, heart_rate=hr, mac=mac, report_id=args.report_id
    )
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
