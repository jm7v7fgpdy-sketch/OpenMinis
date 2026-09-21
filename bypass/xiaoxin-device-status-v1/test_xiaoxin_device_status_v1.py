#!/usr/bin/env python3
"""Tests for the isolated XIAOXIN_DEVICE_STATUS_V1 read-only adapter.

Does not import or modify iOS native offloads, phone-harness, Slack routing,
or any production path. Fixtures are synthetic; they are not health records.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import xiaoxin_device_status_v1 as adapter  # noqa: E402


GENERATED = datetime(2026, 9, 21, 8, 52, 0, tzinfo=timezone.utc)
REPORT_ID = "11111111-2222-4333-8444-555555555555"


def _phone_ok(**overrides):
    observed = GENERATED - timedelta(seconds=12)
    base = {
        "ok": True,
        "tool": "apple-device",
        "action": "info",
        "timestamp": observed.isoformat(),
        "data": {
            "device": {
                "model": "iPhone",
                "system_version": "18.6.2",
            },
            "machine": "iPhone17,1",
            "battery": {
                "level_percent": 81,
                "state": "unplugged",
            },
        },
    }
    base.update(overrides)
    return base


def _steps_ok(steps=4321, days=None, **overrides):
    queried = GENERATED - timedelta(seconds=5)
    if days is None:
        days = [{"date": "2026-09-21T00:00:00+08:00", "steps": steps}]
    base = {
        "ok": True,
        "tool": "apple-healthkit",
        "action": "steps",
        "timestamp": queried.isoformat(),
        "data": {
            "days": days,
            "total": steps,
            "range": {
                "start": "2026-09-21T00:00:00+08:00",
                "end": queried.isoformat(),
            },
        },
    }
    base.update(overrides)
    return base


def _hr_ok(bpm=72, age_seconds=60, **overrides):
    observed = GENERATED - timedelta(seconds=age_seconds)
    queried = GENERATED - timedelta(seconds=4)
    base = {
        "ok": True,
        "tool": "apple-healthkit",
        "action": "heart-rate",
        "timestamp": queried.isoformat(),
        "data": {
            "samples": [
                {
                    "date": observed.isoformat(),
                    "bpm": bpm,
                    "source": "fixture",
                }
            ],
            "count": 1,
        },
    }
    base.update(overrides)
    return base


def _mac_ok(**overrides):
    observed = GENERATED - timedelta(seconds=8)
    base = {
        "observed_at": observed.isoformat(),
        "hostname": "mac-mini.local",
        "uptime_seconds": 86400,
        "load_1m": 1.25,
        "disk_free_pct": 42,
    }
    base.update(overrides)
    return base


class CanonicalHashTests(unittest.TestCase):
    def test_integrity_hash_matches_canonical_object_without_integrity(self):
        report = adapter.build_report(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=adapter.phone_from_apple_device(_phone_ok(), GENERATED),
            health=adapter.health_from_apple_health(_steps_ok(), _hr_ok(), GENERATED),
            mac=adapter.mac_from_snapshot(_mac_ok(), GENERATED),
        )
        body = {k: v for k, v in report.items() if k != "integrity"}
        expected = hashlib.sha256(
            adapter.canonical_json(body).encode("utf-8")
        ).hexdigest()
        self.assertEqual(report["integrity"]["canonical_sha256"], expected)
        self.assertEqual(expected, expected.lower())
        self.assertRegex(expected, r"^[0-9a-f]{64}$")

    def test_canonical_json_sorts_keys_and_has_no_whitespace(self):
        encoded = adapter.canonical_json({"b": 1, "a": {"d": None, "c": True}})
        self.assertEqual(encoded, '{"a":{"c":true,"d":null},"b":1}')

    def test_same_report_id_retry_is_byte_equivalent(self):
        kwargs = dict(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=adapter.phone_from_apple_device(_phone_ok(), GENERATED),
            health=adapter.health_from_apple_health(_steps_ok(), _hr_ok(), GENERATED),
            mac=adapter.mac_from_snapshot(_mac_ok(), GENERATED),
        )
        first = adapter.encode_report(adapter.build_report(**kwargs))
        second = adapter.encode_report(adapter.build_report(**kwargs))
        self.assertEqual(first, second)
        self.assertEqual(
            json.loads(first)["integrity"]["canonical_sha256"],
            json.loads(second)["integrity"]["canonical_sha256"],
        )

    def test_same_report_id_different_body_has_different_hash(self):
        phone_a = adapter.phone_from_apple_device(_phone_ok(), GENERATED)
        phone_b = adapter.phone_from_apple_device(
            _phone_ok(), GENERATED
        )
        phone_b = dict(phone_b)
        phone_b["battery_pct"] = 10
        a = adapter.build_report(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=phone_a,
            health=adapter.health_from_apple_health(_steps_ok(), _hr_ok(), GENERATED),
            mac=adapter.mac_from_snapshot(_mac_ok(), GENERATED),
        )
        b = adapter.build_report(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=phone_b,
            health=adapter.health_from_apple_health(_steps_ok(), _hr_ok(), GENERATED),
            mac=adapter.mac_from_snapshot(_mac_ok(), GENERATED),
        )
        self.assertNotEqual(
            a["integrity"]["canonical_sha256"],
            b["integrity"]["canonical_sha256"],
        )


class SchemaAndFreshnessTests(unittest.TestCase):
    def test_fresh_report_has_required_keys_and_no_guessed_reason(self):
        report = adapter.build_report(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=adapter.phone_from_apple_device(_phone_ok(), GENERATED),
            health=adapter.health_from_apple_health(_steps_ok(), _hr_ok(), GENERATED),
            mac=adapter.mac_from_snapshot(_mac_ok(), GENERATED),
        )
        self.assertEqual(report["type"], "XIAOXIN_DEVICE_STATUS_V1")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["producer"], "xiaoxin+lingxin")
        self.assertEqual(report["phone"]["status"], "fresh")
        self.assertEqual(report["health"]["status"], "fresh")
        self.assertEqual(report["mac"]["status"], "fresh")
        self.assertIsNone(report["phone"]["reason_code"])
        self.assertIsNone(report["health"]["reason_code"])
        self.assertIsNone(report["mac"]["reason_code"])
        self.assertEqual(report["phone"]["charging"], "no")
        self.assertEqual(report["phone"]["model"], "iPhone17,1")
        self.assertEqual(report["phone"]["os_version"], "18.6.2")
        self.assertEqual(report["phone"]["battery_pct"], 81)
        self.assertEqual(report["health"]["steps_today"]["value"], 4321)
        self.assertEqual(report["health"]["latest_heart_rate_bpm"]["value"], 72)

    def test_phone_older_than_300s_is_stale(self):
        observed = (GENERATED - timedelta(seconds=301)).isoformat()
        payload = _phone_ok()
        payload["timestamp"] = observed
        phone = adapter.phone_from_apple_device(payload, GENERATED)
        self.assertEqual(phone["status"], "stale")
        self.assertEqual(phone["age_seconds"], 301)
        self.assertIsNone(phone["reason_code"])

    def test_heart_rate_older_than_15_minutes_is_null_with_reason(self):
        health = adapter.health_from_apple_health(
            _steps_ok(), _hr_ok(age_seconds=15 * 60 + 1), GENERATED
        )
        self.assertIsNone(health["latest_heart_rate_bpm"]["value"])
        self.assertIsNone(health["latest_heart_rate_bpm"]["observed_at"])
        self.assertEqual(health["reason_code"], "NO_RECENT_SAMPLE")
        self.assertEqual(health["steps_today"]["value"], 4321)

    def test_empty_steps_days_leaves_steps_null(self):
        health = adapter.health_from_apple_health(
            _steps_ok(days=[], total=0), _hr_ok(), GENERATED
        )
        self.assertIsNone(health["steps_today"]["value"])
        self.assertIsNone(health["steps_today"]["source_last_sample_at"])
        self.assertEqual(health["latest_heart_rate_bpm"]["value"], 72)

    def test_permission_denied_maps_reason_and_nulls_values(self):
        denied = {
            "ok": False,
            "tool": "apple-healthkit",
            "action": "steps",
            "error": {
                "code": "authorization_denied",
                "message": "HealthKit access not granted",
            },
            "timestamp": GENERATED.isoformat(),
        }
        health = adapter.health_from_apple_health(denied, denied, GENERATED)
        self.assertEqual(health["status"], "unavailable")
        self.assertEqual(health["reason_code"], "PERMISSION_DENIED")
        self.assertIsNone(health["queried_at"])
        self.assertIsNone(health["steps_today"]["value"])
        self.assertIsNone(health["latest_heart_rate_bpm"]["value"])

    def test_reason_codes_are_closed_set(self):
        self.assertEqual(
            adapter.REASON_CODES,
            frozenset(
                {
                    "PERMISSION_DENIED",
                    "SOURCE_UNREACHABLE",
                    "NO_RECENT_SAMPLE",
                    "QUERY_FAILED",
                    "TIMEOUT",
                    "NOT_CONFIGURED",
                }
            ),
        )
        phone = adapter.unavailable_phone("SOURCE_UNREACHABLE")
        self.assertEqual(phone["status"], "unavailable")
        self.assertIsNone(phone["model"])
        self.assertIsNone(phone["battery_pct"])
        self.assertIsNone(phone["charging"])
        self.assertIsNone(phone["observed_at"])
        with self.assertRaises(ValueError):
            adapter.unavailable_phone("NETWORK_GLITCH")


class ReadOnlyCollectorTests(unittest.TestCase):
    def test_live_collect_does_not_invoke_healthkit_writes(self):
        calls = []

        def fake_which(name):
            return None

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("subprocess must not run when binaries are absent")

        with mock.patch.object(adapter.shutil, "which", side_effect=fake_which), mock.patch.object(
            adapter.subprocess, "run", side_effect=fake_run
        ):
            report = adapter.collect_live(
                report_id=REPORT_ID,
                generated_at=GENERATED,
            )
        self.assertEqual(calls, [])
        self.assertEqual(report["phone"]["status"], "unavailable")
        self.assertEqual(report["health"]["status"], "unavailable")
        self.assertEqual(report["mac"]["status"], "unavailable")
        self.assertEqual(report["phone"]["reason_code"], "SOURCE_UNREACHABLE")
        self.assertEqual(report["health"]["reason_code"], "SOURCE_UNREACHABLE")
        self.assertEqual(report["mac"]["reason_code"], "SOURCE_UNREACHABLE")
        for argv in adapter.READ_ONLY_ARGV:
            joined = " ".join(argv)
            self.assertNotIn(" log", " " + joined)
            self.assertNotIn(" delete", " " + joined)
            self.assertFalse(any(part in {"log", "delete", "log-blood-pressure"} for part in argv))

    def test_validate_report_recomputes_hash(self):
        report = adapter.build_report(
            report_id=REPORT_ID,
            generated_at=GENERATED,
            phone=adapter.unavailable_phone("SOURCE_UNREACHABLE"),
            health=adapter.unavailable_health("SOURCE_UNREACHABLE"),
            mac=adapter.unavailable_mac("SOURCE_UNREACHABLE"),
        )
        ok, detail = adapter.validate_report(report)
        self.assertTrue(ok, detail)
        report["integrity"]["canonical_sha256"] = "0" * 64
        ok, detail = adapter.validate_report(report)
        self.assertFalse(ok)
        self.assertEqual(detail, "HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
