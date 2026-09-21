#!/usr/bin/env python3
"""Unit tests for XIAOXIN_DEVICE_STATUS_V1 schema + canonical hash helpers.

Fixtures are synthetic. No real device names, UUIDs, HealthKit sources,
or health records from a person.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone, timedelta

from xiaoxin_device_status import (
    FRESH_MAX_AGE_SECONDS,
    HR_RECENT_MAX_SECONDS,
    REASON_CODES,
    SCHEMA_TYPE,
    SLACK_SUMMARY_PREFIX,
    build_report,
    canonical_dumps,
    format_instinct_slack,
    integrity_sha256,
    map_mac_snapshot,
)


NOW = datetime(2026, 9, 21, 17, 6, 0, tzinfo=timezone(timedelta(hours=8)))
PHONE_TS = "2026-09-21T17:05:50+08:00"
HEALTH_TS = "2026-09-21T17:05:55+08:00"
HR_TS = "2026-09-21T17:00:00+08:00"


def _phone_ok():
    return {
        "ok": True,
        "tool": "apple-device",
        "action": "info",
        "timestamp": PHONE_TS,
        "data": {
            "device": {
                "name": "REDACTED_DEVICE_NAME",
                "system_name": "iOS",
                "system_version": "18.6.2",
                "model": "iPhone",
                "localized_model": "iPhone",
                "identifier_for_vendor": "00000000-0000-0000-0000-000000000000",
                "user_interface_idiom": "phone",
            },
            "machine": "iPhone17,1",
            "os_version": "Version 18.6.2 (Build 22G100)",
            "battery": {
                "level": 0.64,
                "level_percent": 64,
                "state": "charging",
                "monitoring_enabled": True,
            },
        },
    }


def _steps_ok(total=8432):
    return {
        "ok": True,
        "tool": "apple-healthkit",
        "action": "steps",
        "timestamp": HEALTH_TS,
        "data": {
            "days": [{"date": "2026-09-21T00:00:00+08:00", "steps": total}],
            "total": total,
            "range": {
                "start": "2026-09-21T00:00:00+08:00",
                "end": "2026-09-22T00:00:00+08:00",
            },
        },
    }


def _hr_ok(bpm=72, date=HR_TS, samples=None):
    if samples is None:
        samples = [{"date": date, "bpm": bpm, "source": "REDACTED_SOURCE"}]
    return {
        "ok": True,
        "tool": "apple-healthkit",
        "action": "heart-rate",
        "timestamp": HEALTH_TS,
        "data": {
            "samples": samples,
            "count": len(samples),
            "range": {
                "start": "2026-09-20T00:00:00+08:00",
                "end": HEALTH_TS,
            },
        },
    }


def _err(tool, action, code, message="denied"):
    return {
        "ok": False,
        "tool": tool,
        "action": action,
        "timestamp": HEALTH_TS,
        "error": {"code": code, "message": message},
    }


class CanonicalHashTests(unittest.TestCase):
    def test_canonical_dumps_sorts_keys_and_has_no_whitespace(self):
        raw = {"b": 1, "a": {"z": None, "y": 2}}
        dumped = canonical_dumps(raw)
        self.assertEqual(dumped, '{"a":{"y":2,"z":null},"b":1}')

    def test_integrity_is_sha256_of_canonical_json_without_integrity(self):
        body = {
            "type": "XIAOXIN_DEVICE_STATUS_V1",
            "schema_version": 1,
            "producer": "xiaoxin+lingxin",
        }
        expected = hashlib.sha256(
            '{"producer":"xiaoxin+lingxin","schema_version":1,'
            '"type":"XIAOXIN_DEVICE_STATUS_V1"}'.encode("utf-8")
        ).hexdigest()
        self.assertEqual(integrity_sha256(body), expected)
        self.assertEqual(expected, expected.lower())
        self.assertEqual(len(expected), 64)

    def test_integrity_ignores_integrity_key_if_present(self):
        body = {"schema_version": 1, "integrity": {"canonical_sha256": "deadbeef"}}
        self.assertEqual(
            integrity_sha256(body),
            hashlib.sha256(b'{"schema_version":1}').hexdigest(),
        )


class SchemaMappingTests(unittest.TestCase):
    def test_happy_path_phone_and_health_fresh_no_pii(self):
        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(),
            now=NOW,
            report_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(report["type"], "XIAOXIN_DEVICE_STATUS_V1")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["producer"], "xiaoxin+lingxin")
        self.assertEqual(report["generated_at"], "2026-09-21T17:06:00+08:00")

        phone = report["phone"]
        self.assertEqual(phone["status"], "fresh")
        self.assertEqual(phone["observed_at"], PHONE_TS)
        self.assertEqual(phone["age_seconds"], 10)
        self.assertEqual(phone["model"], "iPhone17,1")
        self.assertEqual(phone["os_version"], "18.6.2")
        self.assertEqual(phone["battery_pct"], 64)
        self.assertEqual(phone["charging"], "yes")
        self.assertIsNone(phone["reason_code"])
        blob = json.dumps(report)
        self.assertNotIn("REDACTED_DEVICE_NAME", blob)
        self.assertNotIn("00000000-0000-0000-0000-000000000000", blob)
        self.assertNotIn("identifier_for_vendor", blob)
        self.assertNotIn("REDACTED_SOURCE", blob)

        health = report["health"]
        self.assertEqual(health["status"], "fresh")
        self.assertEqual(health["queried_at"], HEALTH_TS)
        self.assertEqual(health["steps_today"]["value"], 8432)
        self.assertIsNone(health["steps_today"]["source_last_sample_at"])
        self.assertEqual(health["latest_heart_rate_bpm"]["value"], 72)
        self.assertEqual(health["latest_heart_rate_bpm"]["observed_at"], HR_TS)
        self.assertIsNone(health["reason_code"])

        mac = report["mac"]
        self.assertEqual(mac["status"], "unavailable")
        self.assertEqual(mac["reason_code"], "NOT_CONFIGURED")
        self.assertIsNone(mac["observed_at"])
        self.assertEqual(mac["age_seconds"], 0)
        self.assertIsNone(mac["hostname"])
        self.assertEqual(mac["uptime_seconds"], 0)
        self.assertEqual(mac["load_1m"], 0)
        self.assertEqual(mac["disk_free_pct"], 0)

        digest = report["integrity"]["canonical_sha256"]
        self.assertEqual(digest, integrity_sha256(report))
        self.assertEqual(digest, digest.lower())
        without = {k: v for k, v in report.items() if k != "integrity"}
        self.assertEqual(digest, integrity_sha256(without))

    def test_charging_maps_unplugged_to_no_and_unknown_to_unknown(self):
        phone = _phone_ok()
        phone["data"]["battery"]["state"] = "unplugged"
        report = build_report(phone=phone, steps=_steps_ok(), heart_rate=_hr_ok(), now=NOW)
        self.assertEqual(report["phone"]["charging"], "no")

        phone["data"]["battery"]["state"] = "unknown"
        report = build_report(phone=phone, steps=_steps_ok(), heart_rate=_hr_ok(), now=NOW)
        self.assertEqual(report["phone"]["charging"], "unknown")

        phone["data"]["battery"]["state"] = "full"
        report = build_report(phone=phone, steps=_steps_ok(), heart_rate=_hr_ok(), now=NOW)
        self.assertEqual(report["phone"]["charging"], "yes")

    def test_phone_stale_when_age_over_300s(self):
        phone = _phone_ok()
        phone["timestamp"] = "2026-09-21T16:50:00+08:00"
        report = build_report(
            phone=phone, steps=_steps_ok(), heart_rate=_hr_ok(), now=NOW
        )
        self.assertGreater(report["phone"]["age_seconds"], FRESH_MAX_AGE_SECONDS)
        self.assertEqual(report["phone"]["status"], "stale")

    def test_health_permission_denied_maps_reason_code(self):
        report = build_report(
            phone=_phone_ok(),
            steps=_err("apple-healthkit", "steps", "authorization_denied"),
            heart_rate=_err("apple-healthkit", "heart-rate", "authorization_denied"),
            now=NOW,
        )
        self.assertEqual(report["health"]["status"], "unavailable")
        self.assertEqual(report["health"]["reason_code"], "PERMISSION_DENIED")
        self.assertEqual(report["health"]["steps_today"]["value"], 0)
        self.assertEqual(report["health"]["latest_heart_rate_bpm"]["value"], 0)

    def test_query_timeout_maps_to_timeout(self):
        report = build_report(
            phone=_phone_ok(),
            steps=_err("apple-healthkit", "steps", "query_timeout", "15s"),
            heart_rate=_hr_ok(),
            now=NOW,
        )
        self.assertEqual(report["health"]["reason_code"], "TIMEOUT")
        self.assertEqual(report["health"]["status"], "unavailable")

    def test_missing_phone_payload_is_source_unreachable(self):
        report = build_report(
            phone=None, steps=_steps_ok(), heart_rate=_hr_ok(), now=NOW
        )
        self.assertEqual(report["phone"]["status"], "unavailable")
        self.assertEqual(report["phone"]["reason_code"], "SOURCE_UNREACHABLE")
        self.assertIsNone(report["phone"]["model"])

    def test_no_hr_samples_is_no_recent_sample(self):
        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(samples=[]),
            now=NOW,
        )
        self.assertEqual(report["health"]["reason_code"], "NO_RECENT_SAMPLE")
        self.assertEqual(report["health"]["latest_heart_rate_bpm"]["value"], 0)
        self.assertIsNone(report["health"]["latest_heart_rate_bpm"]["observed_at"])
        self.assertEqual(report["health"]["status"], "fresh")
        self.assertEqual(report["health"]["steps_today"]["value"], 8432)

    def test_hr_older_than_15_minutes_is_no_recent_sample(self):
        old = "2026-09-21T16:40:00+08:00"
        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(bpm=88, date=old),
            now=NOW,
        )
        age = (NOW - datetime.fromisoformat(old)).total_seconds()
        self.assertGreater(age, HR_RECENT_MAX_SECONDS)
        self.assertEqual(report["health"]["reason_code"], "NO_RECENT_SAMPLE")
        self.assertEqual(report["health"]["latest_heart_rate_bpm"]["value"], 88)
        self.assertEqual(report["health"]["latest_heart_rate_bpm"]["observed_at"], old)
        self.assertEqual(report["health"]["status"], "fresh")

    def test_reason_codes_are_the_closed_set(self):
        self.assertEqual(
            REASON_CODES,
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

    def test_mac_passthrough_from_lingxin_stub(self):
        mac = {
            "status": "fresh",
            "observed_at": "2026-09-21T17:05:40+08:00",
            "age_seconds": 20,
            "hostname": "AndersendeMini",
            "uptime_seconds": 86400,
            "load_1m": 0.42,
            "disk_free_pct": 37,
            "reason_code": None,
        }
        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(),
            mac=mac,
            now=NOW,
        )
        self.assertEqual(report["mac"]["hostname"], "AndersendeMini")
        self.assertEqual(report["mac"]["status"], "fresh")
        self.assertEqual(report["mac"]["load_1m"], 0.42)

    def test_andersende_snapshot_maps_to_fresh_mac_and_is_hashed(self):
        snapshot = {
            "source": "andersende-mini",
            "hostname": "AndersendeMini",
            "timestamp": "2026-09-21T17:05:40+08:00",
            "uptime_seconds": 86400,
            "loadavg": [0.42, 0.50, 0.55],
            "disk_free_pct": 37,
        }
        mac = map_mac_snapshot(snapshot, NOW)
        self.assertEqual(mac["status"], "fresh")
        self.assertEqual(mac["hostname"], "AndersendeMini")
        self.assertEqual(mac["age_seconds"], 20)
        self.assertEqual(mac["load_1m"], 0.42)
        self.assertIsNone(mac["reason_code"])
        self.assertNotIn("loadavg", mac)
        self.assertNotIn("source", mac)

        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(),
            mac=snapshot,
            now=NOW,
            report_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(report["mac"]["hostname"], "AndersendeMini")
        without_mac = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(),
            now=NOW,
            report_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertNotEqual(
            report["integrity"]["canonical_sha256"],
            without_mac["integrity"]["canonical_sha256"],
        )
        self.assertEqual(
            report["integrity"]["canonical_sha256"], integrity_sha256(report)
        )


class InstinctSlackTests(unittest.TestCase):
    def test_slack_summary_is_single_message_for_instinct(self):
        report = build_report(
            phone=_phone_ok(),
            steps=_steps_ok(),
            heart_rate=_hr_ok(),
            mac={
                "hostname": "AndersendeMini",
                "observed_at": "2026-09-21T17:05:40+08:00",
                "uptime_seconds": 86400,
                "load_1m": 0.42,
                "disk_free_pct": 37,
            },
            now=NOW,
            report_id="00000000-0000-4000-8000-000000000001",
        )
        text = format_instinct_slack(report)
        self.assertTrue(text.startswith(SLACK_SUMMARY_PREFIX))
        self.assertEqual(SLACK_SUMMARY_PREFIX, "@Instinct [XIAOXIN_DEVICE_STATUS_V1]")
        self.assertIn(SCHEMA_TYPE, text)
        self.assertIn("AndersendeMini", text)
        self.assertIn("iPhone17,1", text)
        self.assertIn("8432", text)
        self.assertNotIn("REDACTED_DEVICE_NAME", text)
        self.assertNotIn("webhook", text.lower())
        self.assertNotIn("https://hooks.slack.com", text)
        self.assertEqual(text.count("@Instinct"), 1)


if __name__ == "__main__":
    unittest.main()
