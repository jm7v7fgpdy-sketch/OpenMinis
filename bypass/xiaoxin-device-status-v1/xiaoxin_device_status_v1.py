#!/usr/bin/env python3
"""Isolated read-only adapter for XIAOXIN_DEVICE_STATUS_V1.

Parses already-approved apple-device / apple-healthkit JSON envelopes and an
optional Mac snapshot into the fixed v1 schema, then stamps a RFC8785-style
canonical SHA-256. This module must not write HealthKit, control the phone,
or touch production offloads / Slack routing.

Live collection only execs an allowlisted read-only argv set. Missing
binaries become SOURCE_UNREACHABLE; values are never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Tuple

TYPE = "XIAOXIN_DEVICE_STATUS_V1"
SCHEMA_VERSION = 1
PRODUCER = "xiaoxin+lingxin"

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

PHONE_FRESH_SECONDS = 300
MAC_FRESH_SECONDS = 300
HEALTH_QUERY_FRESH_SECONDS = 300
HEART_RATE_MAX_AGE_SECONDS = 15 * 60

FORBIDDEN_SUBCOMMANDS = frozenset({"log", "delete", "log-blood-pressure"})

# Documented allowlist. Collector will not pass any other argv to subprocess.
READ_ONLY_ARGV = (
    ("apple-device", "info"),
    ("apple-healthkit", "steps", "--today"),
    ("apple-healthkit", "heart-rate", "--days", "1", "--limit", "1"),
    ("apple-health", "steps", "--today"),
    ("apple-health", "heart-rate", "--days", "1", "--limit", "1"),
)


def _require_reason(reason_code: str) -> str:
    if reason_code not in REASON_CODES:
        raise ValueError("reason_code must be one of %s" % sorted(REASON_CODES))
    return reason_code


def canonical_json(value: Any) -> str:
    """RFC8785 / JCS-style canonical JSON for the constrained v1 schema."""

    def encode(node: Any) -> str:
        if node is None:
            return "null"
        if node is True:
            return "true"
        if node is False:
            return "false"
        if isinstance(node, str):
            return _canon_str(node)
        if isinstance(node, int) and not isinstance(node, bool):
            return str(node)
        if isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                raise ValueError("non-finite numbers are not allowed")
            return json.dumps(node, ensure_ascii=True)
        if isinstance(node, list):
            return "[" + ",".join(encode(item) for item in node) + "]"
        if isinstance(node, dict):
            keys = sorted(node.keys(), key=lambda k: k.encode("utf-16-be"))
            parts = [_canon_str(key) + ":" + encode(node[key]) for key in keys]
            return "{" + ",".join(parts) + "}"
        raise TypeError("unsupported type %s" % type(node).__name__)

    return encode(value)


def _canon_str(text: str) -> str:
    chunks = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            chunks.append('\\"')
        elif ch == "\\":
            chunks.append("\\\\")
        elif ch == "\b":
            chunks.append("\\b")
        elif ch == "\f":
            chunks.append("\\f")
        elif ch == "\n":
            chunks.append("\\n")
        elif ch == "\r":
            chunks.append("\\r")
        elif ch == "\t":
            chunks.append("\\t")
        elif code < 0x20:
            chunks.append("\\u%04x" % code)
        else:
            chunks.append(ch)
    chunks.append('"')
    return "".join(chunks)


def parse_rfc3339(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime must include an offset")
        return value
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("RFC3339 timestamp must include an offset")
    return parsed


def format_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must include an offset")
    return value.replace(microsecond=0).isoformat()


def _age_seconds(generated_at: datetime, observed_at: Optional[datetime]) -> Optional[int]:
    if observed_at is None:
        return None
    delta = (generated_at - observed_at).total_seconds()
    return int(delta) if delta >= 0 else 0


def _freshness(generated_at: datetime, observed_at: Optional[datetime], limit: int) -> str:
    age = _age_seconds(generated_at, observed_at)
    if age is None:
        return "unavailable"
    return "fresh" if age <= limit else "stale"


def unavailable_phone(reason_code: str) -> dict:
    _require_reason(reason_code)
    return {
        "status": "unavailable",
        "observed_at": None,
        "age_seconds": None,
        "model": None,
        "os_version": None,
        "battery_pct": None,
        "charging": None,
        "reason_code": reason_code,
    }


def unavailable_health(reason_code: str) -> dict:
    _require_reason(reason_code)
    return {
        "status": "unavailable",
        "queried_at": None,
        "steps_today": {"value": None, "source_last_sample_at": None},
        "latest_heart_rate_bpm": {"value": None, "observed_at": None},
        "reason_code": reason_code,
    }


def unavailable_mac(reason_code: str) -> dict:
    _require_reason(reason_code)
    return {
        "status": "unavailable",
        "observed_at": None,
        "age_seconds": None,
        "hostname": None,
        "uptime_seconds": None,
        "load_1m": None,
        "disk_free_pct": None,
        "reason_code": reason_code,
    }


def map_bridge_error(payload: Optional[Mapping[str, Any]]) -> str:
    if not payload:
        return "SOURCE_UNREACHABLE"
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or "").lower()
    if code in {"authorization_denied", "authorization_not_determined"}:
        return "PERMISSION_DENIED"
    if code in {"not_available"}:
        return "SOURCE_UNREACHABLE"
    if code in {"no_data"}:
        return "NO_RECENT_SAMPLE"
    if "timeout" in code or "timeout" in message:
        return "TIMEOUT"
    if payload.get("ok") is False:
        return "QUERY_FAILED"
    return "QUERY_FAILED"


def _charging_from_state(state: Any) -> Optional[str]:
    if state is None:
        return None
    value = str(state).lower()
    if value in {"charging", "full", "yes"}:
        return "yes"
    if value in {"unplugged", "no"}:
        return "no"
    return "unknown"


def phone_from_apple_device(payload: Optional[Mapping[str, Any]], generated_at: datetime) -> dict:
    generated_at = parse_rfc3339(generated_at)
    if not payload:
        return unavailable_phone("SOURCE_UNREACHABLE")
    if payload.get("ok") is False:
        return unavailable_phone(map_bridge_error(payload))
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    if not isinstance(data, Mapping):
        return unavailable_phone("QUERY_FAILED")
    device = data.get("device") if isinstance(data.get("device"), Mapping) else {}
    battery = data.get("battery") if isinstance(data.get("battery"), Mapping) else {}
    observed = parse_rfc3339(payload.get("timestamp")) or generated_at
    model = data.get("machine") or device.get("model")
    os_version = device.get("system_version") or data.get("os_version")
    level = battery.get("level_percent")
    if level is None:
        battery_pct = None
    else:
        try:
            battery_pct = int(level)
        except (TypeError, ValueError):
            battery_pct = None
    charging = _charging_from_state(battery.get("state"))
    if model in (None, "") and os_version in (None, "") and battery_pct is None:
        return unavailable_phone("NO_RECENT_SAMPLE")
    return {
        "status": _freshness(generated_at, observed, PHONE_FRESH_SECONDS),
        "observed_at": format_rfc3339(observed),
        "age_seconds": _age_seconds(generated_at, observed),
        "model": str(model) if model not in (None, "") else None,
        "os_version": str(os_version) if os_version not in (None, "") else None,
        "battery_pct": battery_pct,
        "charging": charging if charging is not None else "unknown",
        "reason_code": None,
    }


def health_from_apple_health(
    steps_payload: Optional[Mapping[str, Any]],
    hr_payload: Optional[Mapping[str, Any]],
    generated_at: datetime,
) -> dict:
    generated_at = parse_rfc3339(generated_at)
    steps_ok = bool(steps_payload) and steps_payload.get("ok") is not False
    hr_ok = bool(hr_payload) and hr_payload.get("ok") is not False

    if not steps_ok and not hr_ok:
        reason = map_bridge_error(steps_payload or hr_payload)
        if steps_payload and map_bridge_error(steps_payload) == "PERMISSION_DENIED":
            reason = "PERMISSION_DENIED"
        if hr_payload and map_bridge_error(hr_payload) == "PERMISSION_DENIED":
            reason = "PERMISSION_DENIED"
        if not steps_payload and not hr_payload:
            reason = "SOURCE_UNREACHABLE"
        return unavailable_health(reason)

    query_times = []
    for payload, ok in ((steps_payload, steps_ok), (hr_payload, hr_ok)):
        if ok and payload:
            ts = parse_rfc3339(payload.get("timestamp"))
            if ts is not None:
                query_times.append(ts)
    queried_at = min(query_times) if query_times else generated_at

    steps_value = None
    steps_last = None
    if steps_ok and steps_payload:
        data = steps_payload.get("data") if isinstance(steps_payload.get("data"), Mapping) else {}
        days = data.get("days") if isinstance(data.get("days"), list) else []
        if days:
            last_day = days[-1] if isinstance(days[-1], Mapping) else {}
            if "steps" in last_day:
                try:
                    steps_value = int(last_day["steps"])
                except (TypeError, ValueError):
                    steps_value = None
            # Existing apple-healthkit steps --today does not expose sample time.
            # Do not invent one from the day bucket or query range.
            steps_last = None

    hr_value = None
    hr_observed = None
    hr_too_old = False
    if hr_ok and hr_payload:
        data = hr_payload.get("data") if isinstance(hr_payload.get("data"), Mapping) else {}
        samples = data.get("samples") if isinstance(data.get("samples"), list) else []
        latest = None
        latest_dt = None
        for sample in samples:
            if not isinstance(sample, Mapping):
                continue
            sample_dt = parse_rfc3339(sample.get("date") or sample.get("observed_at"))
            if sample_dt is None:
                continue
            if latest_dt is None or sample_dt > latest_dt:
                latest_dt = sample_dt
                latest = sample
        if latest is not None and latest_dt is not None:
            age = _age_seconds(generated_at, latest_dt)
            if age is not None and age <= HEART_RATE_MAX_AGE_SECONDS:
                try:
                    hr_value = int(round(float(latest.get("bpm"))))
                    hr_observed = format_rfc3339(latest_dt)
                except (TypeError, ValueError):
                    hr_value = None
                    hr_observed = None
            else:
                hr_too_old = True

    reason = None
    if hr_too_old and hr_value is None:
        reason = "NO_RECENT_SAMPLE"
    if steps_value is None and hr_value is None:
        return unavailable_health("NO_RECENT_SAMPLE")

    return {
        "status": _freshness(generated_at, queried_at, HEALTH_QUERY_FRESH_SECONDS),
        "queried_at": format_rfc3339(queried_at),
        "steps_today": {
            "value": steps_value,
            "source_last_sample_at": steps_last,
        },
        "latest_heart_rate_bpm": {
            "value": hr_value,
            "observed_at": hr_observed,
        },
        "reason_code": reason,
    }


def mac_from_snapshot(snapshot: Optional[Mapping[str, Any]], generated_at: datetime) -> dict:
    generated_at = parse_rfc3339(generated_at)
    if not snapshot:
        return unavailable_mac("SOURCE_UNREACHABLE")
    observed = parse_rfc3339(snapshot.get("observed_at"))
    if observed is None:
        return unavailable_mac("NO_RECENT_SAMPLE")

    def _num(key: str, as_int: bool = False):
        value = snapshot.get(key)
        if value is None:
            return None
        try:
            return int(value) if as_int else float(value)
        except (TypeError, ValueError):
            return None

    hostname = snapshot.get("hostname")
    uptime = _num("uptime_seconds", as_int=True)
    load_1m = _num("load_1m", as_int=False)
    disk_free = _num("disk_free_pct", as_int=False)
    if disk_free is not None and float(disk_free).is_integer():
        disk_free = int(disk_free)
    return {
        "status": _freshness(generated_at, observed, MAC_FRESH_SECONDS),
        "observed_at": format_rfc3339(observed),
        "age_seconds": _age_seconds(generated_at, observed),
        "hostname": str(hostname) if hostname not in (None, "") else None,
        "uptime_seconds": uptime,
        "load_1m": load_1m,
        "disk_free_pct": disk_free,
        "reason_code": None,
    }


def build_report(
    *,
    report_id: str,
    generated_at: datetime,
    phone: Mapping[str, Any],
    health: Mapping[str, Any],
    mac: Mapping[str, Any],
) -> dict:
    generated_at = parse_rfc3339(generated_at)
    report = {
        "type": TYPE,
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "generated_at": format_rfc3339(generated_at),
        "producer": PRODUCER,
        "phone": dict(phone),
        "health": dict(health),
        "mac": dict(mac),
    }
    digest = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    report["integrity"] = {"canonical_sha256": digest}
    return report


def encode_report(report: Mapping[str, Any]) -> str:
    return canonical_json(report)


def validate_report(report: Mapping[str, Any]) -> Tuple[bool, str]:
    if report.get("type") != TYPE:
        return False, "TYPE_MISMATCH"
    if report.get("schema_version") != SCHEMA_VERSION:
        return False, "SCHEMA_MISMATCH"
    if report.get("producer") != PRODUCER:
        return False, "PRODUCER_MISMATCH"
    for section in ("phone", "health", "mac"):
        block = report.get(section)
        if not isinstance(block, Mapping) or "status" not in block:
            return False, "MISSING_STATUS"
        if block["status"] not in {"fresh", "stale", "unavailable"}:
            return False, "BAD_STATUS"
        reason = block.get("reason_code")
        if reason is not None and reason not in REASON_CODES:
            return False, "BAD_REASON"
    integrity = report.get("integrity") if isinstance(report.get("integrity"), Mapping) else {}
    claimed = integrity.get("canonical_sha256")
    body = {key: value for key, value in report.items() if key != "integrity"}
    actual = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    if claimed != actual:
        return False, "HASH_MISMATCH"
    return True, "OK"


def _assert_readonly(argv: Tuple[str, ...]) -> None:
    if any(part in FORBIDDEN_SUBCOMMANDS for part in argv):
        raise RuntimeError("refusing HealthKit write subcommand")
    if argv not in READ_ONLY_ARGV:
        raise RuntimeError("argv is not on the read-only allowlist")


def _run_readonly(argv: Tuple[str, ...], timeout: int = 15) -> Tuple[Optional[dict], Optional[str]]:
    _assert_readonly(argv)
    if shutil.which(argv[0]) is None:
        return None, "SOURCE_UNREACHABLE"
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except OSError:
        return None, "SOURCE_UNREACHABLE"
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return None, "QUERY_FAILED"
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None, "QUERY_FAILED"
    if not isinstance(parsed, dict):
        return None, "QUERY_FAILED"
    return parsed, None


def collect_mac_snapshot(generated_at: datetime) -> dict:
    # Mac mini only. Linux / this cloud VM must not be labelled as mac.
    if sys.platform != "darwin":
        return unavailable_mac("SOURCE_UNREACHABLE")
    observed = generated_at
    hostname = None
    try:
        proc = subprocess.run(
            ["hostname"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        hostname = (proc.stdout or "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        hostname = None
    uptime = None
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Darwin: { sec = 123, usec = 0 } ...
        text = proc.stdout or ""
        if "sec" in text:
            digits = "".join(ch if ch.isdigit() else " " for ch in text.split("sec", 1)[1])
            parts = digits.split()
            if parts:
                boot = int(parts[0])
                uptime = int(observed.timestamp()) - boot
                if uptime < 0:
                    uptime = None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        uptime = None
    load_1m = None
    try:
        load_1m = float(os.getloadavg()[0])
    except OSError:
        load_1m = None
    disk_free_pct = None
    try:
        usage = shutil.disk_usage("/")
        if usage.total:
            disk_free_pct = int(round(usage.free * 100.0 / usage.total))
    except OSError:
        disk_free_pct = None
    if hostname is None and uptime is None and load_1m is None and disk_free_pct is None:
        return unavailable_mac("QUERY_FAILED")
    return mac_from_snapshot(
        {
            "observed_at": format_rfc3339(observed),
            "hostname": hostname,
            "uptime_seconds": uptime,
            "load_1m": load_1m,
            "disk_free_pct": disk_free_pct,
        },
        generated_at,
    )


def collect_live(
    *,
    report_id: str,
    generated_at: Optional[datetime] = None,
    timeout: int = 15,
) -> dict:
    generated_at = parse_rfc3339(generated_at) if generated_at else datetime.now(timezone.utc)

    phone_payload, phone_reason = _run_readonly(READ_ONLY_ARGV[0], timeout=timeout)
    if phone_payload is None:
        phone = unavailable_phone(phone_reason or "SOURCE_UNREACHABLE")
    else:
        phone = phone_from_apple_device(phone_payload, generated_at)

    steps_payload = None
    hr_payload = None
    health_reason = None
    for prefix in ("apple-healthkit", "apple-health"):
        steps_argv = (prefix, "steps", "--today")
        hr_argv = (prefix, "heart-rate", "--days", "1", "--limit", "1")
        steps_payload, steps_reason = _run_readonly(steps_argv, timeout=timeout)
        hr_payload, hr_reason = _run_readonly(hr_argv, timeout=timeout)
        if steps_payload is not None or hr_payload is not None:
            health_reason = None
            break
        health_reason = steps_reason or hr_reason or "SOURCE_UNREACHABLE"
        if health_reason != "SOURCE_UNREACHABLE":
            break

    if steps_payload is None and hr_payload is None:
        health = unavailable_health(health_reason or "SOURCE_UNREACHABLE")
    else:
        health = health_from_apple_health(steps_payload, hr_payload, generated_at)

    mac = collect_mac_snapshot(generated_at)
    return build_report(
        report_id=report_id,
        generated_at=generated_at,
        phone=phone,
        health=health,
        mac=mac,
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only XIAOXIN_DEVICE_STATUS_V1 adapter (isolated bypass)."
    )
    parser.add_argument("--collect", action="store_true", help="Query allowlisted read-only bridges")
    parser.add_argument("--report-id", help="UUIDv4; retries must reuse the same id")
    parser.add_argument("--phone-json", help="apple-device info envelope file")
    parser.add_argument("--health-steps-json", help="apple-healthkit steps envelope file")
    parser.add_argument("--health-hr-json", help="apple-healthkit heart-rate envelope file")
    parser.add_argument("--mac-json", help="Mac snapshot file supplied by 領芯")
    args = parser.parse_args(argv)

    report_id = args.report_id or "00000000-0000-4000-8000-000000000000"
    generated_at = datetime.now(timezone.utc)

    def _load(path: Optional[str]) -> Optional[dict]:
        if not path:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise SystemExit("JSON object required")
        return data

    if args.collect:
        report = collect_live(report_id=report_id, generated_at=generated_at)
    else:
        report = build_report(
            report_id=report_id,
            generated_at=generated_at,
            phone=phone_from_apple_device(_load(args.phone_json), generated_at),
            health=health_from_apple_health(
                _load(args.health_steps_json),
                _load(args.health_hr_json),
                generated_at,
            ),
            mac=mac_from_snapshot(_load(args.mac_json), generated_at),
        )
    ok, detail = validate_report(report)
    if not ok:
        sys.stderr.write("validate failed: %s\n" % detail)
        return 1
    sys.stdout.write(encode_report(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
