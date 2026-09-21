# XIAOXIN_DEVICE_STATUS_V1 — summary for 知芯 (Instinct)

**Audience:** 知芯 (Instinct) must **see** phone + computer status in one place.

This is **not** “小芯 reads for herself.” Open Minis is only the collection runtime because `apple-device` / `apple-healthkit` are the existing read-only path for phone + HealthKit. 领芯 already holds a **fresh AndersendeMini** mac snapshot. The mapper merges those sources, hashes the envelope, and emits **one Slack summary** 知芯 can verify:

```
@Instinct [XIAOXIN_DEVICE_STATUS_V1]
```

`PRODUCTION_CHANGED=false`. No Slack routing, webhook, scheduler, phone-harness, or Known-Good changes in this repo. 领芯 posts the printed text on the existing Slack path.

---

## What 知芯 should receive

One message. Prefix exactly `@Instinct [XIAOXIN_DEVICE_STATUS_V1]`, then a JSON fence with the v1 envelope (`phone`, `health`, `mac`, `integrity`).

Synthetic example (no PII, no tokens):

```
@Instinct [XIAOXIN_DEVICE_STATUS_V1]
```json
{
  "generated_at": "2026-09-21T17:06:00+08:00",
  "health": {
    "latest_heart_rate_bpm": {
      "observed_at": "2026-09-21T17:00:00+08:00",
      "value": 72
    },
    "queried_at": "2026-09-21T17:05:55+08:00",
    "reason_code": null,
    "status": "fresh",
    "steps_today": {
      "source_last_sample_at": null,
      "value": 8432
    }
  },
  "integrity": {
    "canonical_sha256": "6312e367daa7024d5b366b02b15693e2fed22df8488325692220d1395fd904d6"
  },
  "mac": {
    "age_seconds": 20,
    "disk_free_pct": 37,
    "hostname": "AndersendeMini",
    "load_1m": 0.42,
    "observed_at": "2026-09-21T17:05:40+08:00",
    "reason_code": null,
    "status": "fresh",
    "uptime_seconds": 86400
  },
  "phone": {
    "age_seconds": 10,
    "battery_pct": 64,
    "charging": "yes",
    "model": "iPhone17,1",
    "observed_at": "2026-09-21T17:05:50+08:00",
    "os_version": "18.6.2",
    "reason_code": null,
    "status": "fresh"
  },
  "producer": "xiaoxin+lingxin",
  "report_id": "00000000-0000-4000-8000-000000000001",
  "schema_version": 1,
  "type": "XIAOXIN_DEVICE_STATUS_V1"
}
```
```

Hash is over compact sorted JSON **without** `integrity`, not over the Slack pretty-print.

---

## How fields get into that JSON

```
Open Minis (one awake turn, read-only)
  apple-device info          → phone
  apple-healthkit steps      → health.steps_today
  apple-healthkit heart-rate → health.latest_heart_rate_bpm
领芯
  AndersendeMini snapshot    → mac (already fresh)
mapper (this branch)
  merge + canonical SHA-256
  print @Instinct [XIAOXIN_DEVICE_STATUS_V1] + JSON
领芯
  post that single string on the existing Slack path → 知芯 verifies
```

### Phone + health (Open Minis guest; do not use `-q`)

Do **not** batch steps + heart-rate (shared date window). Guest name is `apple-healthkit`, not `apple-health`.

```sh
apple-device info --compact > /tmp/xx-phone.json
apple-healthkit steps --today --compact > /tmp/xx-steps.json
apple-healthkit heart-rate --days 1 --limit 1 --compact > /tmp/xx-hr.json
```

| V1 field | Source | Notes |
|---|---|---|
| `phone.observed_at` | envelope `timestamp` | `fresh` if age `<= 300s` |
| `phone.model` | `data.machine` | e.g. `iPhone17,1`. **Not** `device.name` |
| `phone.os_version` | `data.device.system_version` | e.g. `18.6.2` |
| `phone.battery_pct` | `data.battery.level_percent` | int 0–100 |
| `phone.charging` | `data.battery.state` | `charging`/`full` → `yes`; `unplugged` → `no`; else `unknown` |
| `health.queried_at` | later of the two envelope timestamps | `fresh` if query age `<= 300s` |
| `health.steps_today.value` | `data.total` | `--today` daily sum |
| `health.steps_today.source_last_sample_at` | **always `null`** | statistics query has no last-sample time |
| `health.latest_heart_rate_bpm.value` | first `data.samples[].bpm` | newest-first |
| `health.latest_heart_rate_bpm.observed_at` | first `data.samples[].date` | `NO_RECENT_SAMPLE` if missing or `> 15min` |

Forbidden in the Open Minis turn: `log`, `log-blood-pressure`, `delete`, `characteristic`; copying `device.name`, `identifier_for_vendor`, or HealthKit `source` strings.

If HealthKit/offload denies: `health.reason_code=PERMISSION_DENIED`. Existing permission types only.

### Mac (领芯 AndersendeMini snapshot — already fresh)

Pass the snapshot as `--mac`. Mapper keeps only v1 keys (no extra snapshot fields in the envelope):

| V1 `mac` | Snapshot aliases | Notes |
|---|---|---|
| `hostname` | `hostname` / `host` / `computer_name` | example: `AndersendeMini` |
| `observed_at` | `observed_at` / `timestamp` / `generated_at` | RFC3339 with offset |
| `age_seconds` | computed | `now - observed_at`; `fresh` if `<= 300s` |
| `uptime_seconds` | `uptime_seconds` / `uptime` | int |
| `load_1m` | `load_1m` or `loadavg[0]` | |
| `disk_free_pct` | `disk_free_pct` / `disk_free_percent` | int |
| `reason_code` | — | `null` when observed_at present |

A v1 `mac` block is accepted as-is (same keys). If `--mac` is omitted, stub is `unavailable` / `NOT_CONFIGURED` (not the 知芯 path).

### Integrity

1. Object **without** `integrity`.
2. Canonical JSON: `json.dumps(..., ensure_ascii=False, separators=(',', ':'), sort_keys=True)`.
3. `integrity.canonical_sha256` = lowercase SHA-256 hex of those UTF-8 bytes.

Mac is included **before** the hash. Changing AndersendeMini fields changes the digest.

---

## Mapper (isolated; 领芯 runs it)

```sh
python3 tools/xiaoxin_device_status/xiaoxin_device_status.py \
  --phone /tmp/xx-phone.json \
  --steps /tmp/xx-steps.json \
  --hr /tmp/xx-hr.json \
  --mac /tmp/xx-andersende.json
# default --format slack  →  stdout is the @Instinct message
```

`--format json` prints the envelope only. This helper **does not post** to Slack.

| File | Role |
|---|---|
| `tools/xiaoxin_device_status/xiaoxin_device_status.py` | phone/health map, AndersendeMini mac map, hash, Slack text |
| `tools/xiaoxin_device_status/test_xiaoxin_device_status.py` | schema / hash / Instinct prefix tests |
| `docs/xiaoxin-device-status-v1.md` | this playbook |

```sh
cd tools/xiaoxin_device_status && python3 -m unittest test_xiaoxin_device_status -v
```

---

## Bridge errors → `reason_code`

Closed set: `PERMISSION_DENIED`, `SOURCE_UNREACHABLE`, `NO_RECENT_SAMPLE`, `QUERY_FAILED`, `TIMEOUT`, `NOT_CONFIGURED`.

| Condition | Code |
|---|---|
| `authorization_denied` / `authorization_not_determined` | `PERMISSION_DENIED` |
| missing JSON / `not_available` | `SOURCE_UNREACHABLE` |
| `query_timeout` | `TIMEOUT` |
| other bridge failures | `QUERY_FAILED` |
| HR missing or sample age `> 900s` | `NO_RECENT_SAMPLE` |
| no mac snapshot | `NOT_CONFIGURED` |

---

## Out of scope

Scheduling, new Slack bots or routing, production merges, phone-harness / RemotePairing / CoreDevice / keyboard services, second wake chains, HealthKit writes, phone control.
