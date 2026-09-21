# XIAOXIN_DEVICE_STATUS_V1 — one-shot Open Minis bridge

**Verdict:** existing Open Minis native offloads can fill `phone` + `health` without changing `apple-device`, `apple-healthkit`, phone-harness, Slack, or Known-Good.

A single awake iOS Open Minis turn runs three **read-only** guest commands, then this repo’s isolated mapper emits `XIAOXIN_DEVICE_STATUS_V1`. No scheduler, webhook, or second wake chain.

`PRODUCTION_CHANGED=false`. Guest CLI names: `apple-device` and `apple-healthkit` (older sandbox notes said `apple-health`; that string is **not** registered).

Mac fields are filled by 领芯 outside this repo. The mapper inserts a `NOT_CONFIGURED` stub unless `--mac` is passed.

---

## One-shot call path (iOS guest shell)

Do **not** use `-q` / `--quiet`: envelope `timestamp` is `observed_at` / `queried_at`.

Do **not** batch `steps` and `heart-rate` in one `apple-healthkit batch`. Batch shares one date range; `--today` is required for `steps_today` but would clip heart rate to calendar-today. Two commands:

```sh
apple-device info --compact > /tmp/xx-phone.json
apple-healthkit steps --today --compact > /tmp/xx-steps.json
apple-healthkit heart-rate --days 1 --limit 1 --compact > /tmp/xx-hr.json
python3 tools/xiaoxin_device_status/xiaoxin_device_status.py \
  --phone /tmp/xx-phone.json \
  --steps /tmp/xx-steps.json \
  --hr /tmp/xx-hr.json
```

Optional: `--mac /tmp/xx-mac.json` from 领芯.

Offload permissions already in tree: `apple-device` is system/bypass; `apple-healthkit` is privacy (`Bypass` or session grant). OS HealthKit **read** for step count and heart rate must already be allowed. If Minis is not allowed, mapper sets `health.reason_code=PERMISSION_DENIED`. No new permission types.

### Forbidden in this turn

- `apple-healthkit log`, `log-blood-pressure`, `delete` (writes)
- `apple-healthkit characteristic` (DOB / sex / blood type — PII, not in schema)
- Copying `data.device.name` or `identifier_for_vendor` into the report
- Copying HealthKit `source` strings (watch / app names)

---

## Field mapping

### Phone ← `apple-device info` success envelope

| V1 field | Source | Notes |
|---|---|---|
| `phone.observed_at` | envelope `timestamp` | ISO-8601 with local offset (`noff_format_date`) |
| `phone.age_seconds` | `now - observed_at` | `fresh` if `<= 300`, else `stale` |
| `phone.model` | `data.machine` | utsname, e.g. `iPhone17,1`. **Not** `device.name` |
| `phone.os_version` | `data.device.system_version` | e.g. `18.6.2`. Not `data.os_version` (includes build string) |
| `phone.battery_pct` | `data.battery.level_percent` | integer 0–100 |
| `phone.charging` | `data.battery.state` | `charging`/`full` → `yes`; `unplugged` → `no`; else `unknown` |
| `phone.reason_code` | error envelope `error.code` | see table below; `null` on success |

`apple-device battery` alone cannot fill `model` / `os_version`; use `info` (default).

### Health ← `apple-healthkit steps` + `heart-rate`

| V1 field | Source | Notes |
|---|---|---|
| `health.queried_at` | later of the two envelope `timestamp`s | `fresh` if query age `<= 300s` |
| `health.steps_today.value` | `data.total` | daily cumulative for `--today` |
| `health.steps_today.source_last_sample_at` | **always `null`** | statistics query has no last-sample time; do not fake it with midnight |
| `health.latest_heart_rate_bpm.value` | first `data.samples[].bpm` | samples are newest-first |
| `health.latest_heart_rate_bpm.observed_at` | first `data.samples[].date` | |
| `health.reason_code` | see rules | `NO_RECENT_SAMPLE` if no sample **or** sample older than 15 minutes |

Empty HR samples: `value=0`, `observed_at=null`, `reason_code=NO_RECENT_SAMPLE`. Query can still be `fresh`.

### Mac ← 领芯 (stub only here)

Default: `status=unavailable`, `reason_code=NOT_CONFIGURED`, zeros / nulls. 领芯 may replace the block (`hostname`, `uptime_seconds`, `load_1m`, `disk_free_pct`, `observed_at`, `age_seconds`) without adding PII beyond hostname.

---

## Bridge error → `reason_code`

Closed set: `PERMISSION_DENIED`, `SOURCE_UNREACHABLE`, `NO_RECENT_SAMPLE`, `QUERY_FAILED`, `TIMEOUT`, `NOT_CONFIGURED`.

| Bridge `error.code` / condition | V1 `reason_code` |
|---|---|
| `authorization_denied`, `authorization_not_determined`; offload “Not Allowed” | `PERMISSION_DENIED` |
| command missing, JSON absent, `not_available` (HealthKit unavailable) | `SOURCE_UNREACHABLE` |
| `query_timeout` (15s HealthKit wait, including locked-store reads) | `TIMEOUT` |
| `internal_error`, other failures | `QUERY_FAILED` |
| HR missing or sample age `> 900s` | `NO_RECENT_SAMPLE` |
| Mac not supplied | `NOT_CONFIGURED` |

Any health `query_timeout` marks `health.status=unavailable` even if the other health command succeeded.

---

## Envelope + integrity

1. Build the object **without** `integrity`.
2. Canonical JSON: UTF-8, no whitespace, object keys sorted (`json.dumps(..., ensure_ascii=False, separators=(',', ':'), sort_keys=True)` — RFC8785/JCS *concept* for this ASCII-key schema).
3. `integrity.canonical_sha256` = lowercase SHA-256 hex of those bytes.

`type` is `XIAOXIN_DEVICE_STATUS_V1`, `schema_version` is `1`, `producer` is `xiaoxin+lingxin`, `generated_at` is RFC3339 with numeric offset, `report_id` is UUID v4.

---

## Example (synthetic, no PII)

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
    "canonical_sha256": "f34cca888958deaad337bd02fdb8adc87cb1e13a14172bb481de762e5e0858d6"
  },
  "mac": {
    "age_seconds": 0,
    "disk_free_pct": 0,
    "hostname": null,
    "load_1m": 0,
    "observed_at": null,
    "reason_code": "NOT_CONFIGURED",
    "status": "unavailable",
    "uptime_seconds": 0
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

Pretty-print above is for reading. The hash is over the compact sorted JSON **without** `integrity` (see `tools/xiaoxin_device_status/test_xiaoxin_device_status.py`).

---

## Isolated adapter (this branch only)

| File | Role |
|---|---|
| `tools/xiaoxin_device_status/xiaoxin_device_status.py` | Mapper + canonical SHA-256 |
| `tools/xiaoxin_device_status/test_xiaoxin_device_status.py` | Schema / hash / reason-code tests |
| `docs/xiaoxin-device-status-v1.md` | This playbook |

Does not modify `src/ios/NativeOffloads/DeviceOffload.m` or `HealthKitOffload.m`.

```sh
cd tools/xiaoxin_device_status && python3 -m unittest test_xiaoxin_device_status -v
```

---

## Out of scope (explicit)

Scheduling, Slack bots, production merges, phone-harness / RemotePairing / CoreDevice / keyboard services, second wake chains, HealthKit writes, phone control.
