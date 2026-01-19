
# Monitoring Architecture — Alerting, Notifications & Reminder (Cooldown) Guide

_Last updated: 2025-11-05 17:02 UTC_

This document explains, in detail, how the alerting & notification mechanism works in this project, how the **reminder/cooldown** is determined (per client and via environment), how to **diagnose** issues using Docker/Compose commands, and how to **tune** settings (DB/ENV). It also covers incident de-duplication and the unique-constraint behavior you may observe in PostgreSQL logs.

---

## Table of Contents

1. [High-Level Flow](#high-level-flow)
2. [Key Python Components](#key-python-components)
   - [Notification cooldown source of truth](#notification-cooldown-source-of-truth)
   - [`get_remind_seconds(client_id)`](#get_remind_secondsclient_id)
   - [`notify(payload)` — Slack sending task](#notifypayload--slack-sending-task)
   - [`notify_alert(alert_id, remind_after_minutes=None)`](#notify_alertalert_id-remind_after_minutesnone)
   - [HTTP Monitor Service (`http_monitor_service.py`)](#http-monitor-service-http_monitor_servicepy)
3. [Database Layer](#database-layer)
   - [Tables of interest](#tables-of-interest)
   - [Effective reminder per client](#effective-reminder-per-client)
   - [Incident de-duplication & unique constraint](#incident-de-duplication--unique-constraint)
4. [Configuration: Where Reminder Is Declared](#configuration-where-reminder-is-declared)
5. [Diagnosis Playbook — Docker/Compose Commands](#diagnosis-playbook--dockercompose-commands)
   - [Check effective ENV across services](#check-effective-env-across-services)
   - [Check effective reminder **from DB**](#check-effective-reminder-from-db)
   - [Check global helper `get_remind_seconds`](#check-global-helper-get_remind_seconds)
   - [Inspect recent notification logs](#inspect-recent-notification-logs)
   - [Check beat schedule & Celery queues](#check-beat-schedule--celery-queues)
   - [Inspect incidents & duplicates](#inspect-incidents--duplicates)
6. [Adjusting Behavior](#adjusting-behavior)
   - [Change per-client reminder in DB](#change-per-client-reminder-in-db)
   - [Change global fallback in ENV](#change-global-fallback-in-env)
   - [When to restart services](#when-to-restart-services)
7. [FAQ / Troubleshooting](#faq--troubleshooting)
8. [Reference Snippets](#reference-snippets)

---

## High-Level Flow

- **HTTP checks** (and other tasks) run on schedule via **Celery beat**.
- Failures open or keep **Incidents** in `incidents` table (one open per `(client_id, http_target_id)` thanks to a unique index).
- **Notifications** are sent to Slack through `notify(...)` **only if** the **cooldown** (aka reminder interval) allows it.
- The **cooldown** is resolved **per client** via:
  1) `client_settings.reminder_notification_seconds` (if set and >0), else  
  2) `DEFAULT_ALERT_REMINDER_MINUTES` from ENV (converted to seconds), else  
  3) hard default (e.g., 30 minutes).
- Successful sends are tracked in `notification_log` with `status='success'` and `sent_at` timestamp. The **last success** is used to enforce cooldown (both for **alerts** and **incidents**).

---

## Key Python Components

### Notification cooldown source of truth

- **Primary**: `client_settings.reminder_notification_seconds` (per client).
- **Fallback**: `settings.DEFAULT_ALERT_REMINDER_MINUTES` (ENV), converted to seconds.
- **Hard default**: 30 minutes = 1800 seconds (when ENV isn’t available).

### `get_remind_seconds(client_id)`

**File**: `server/app/workers/tasks/notification_tasks.py`

Purpose: return the anti-spam reminder **in seconds**, prioritizing DB per-client value, then ENV.

**Core behavior**:
- No/invalid `client_id` → **ENV fallback**.
- Valid `client_id` → read DB via `ClientSettingsRepository.get_effective_reminder_seconds`.
- DB errors → **ENV fallback**.

> Tip: Add a debug log to see the source explicitly:
```python
logger.debug("reminder_seconds.source", extra={
    "client_id": str(client_id) if client_id else None,
    "seconds": seconds,
    "source": "db" or "env"
})
```

### `notify(payload)` — Slack sending task

**File**: `server/app/workers/tasks/notification_tasks.py`

- Validates payload (Pydantic).
- Resolves **Slack webhook per client** via `ClientSettingsRepository`.
- Writes a `notification_log` entry with `status='pending'` before sending.
- Calls `SlackProvider.send()`.
- On success: writes another `notification_log` with `status='success'` and **`set_sent_at=True`**.
- On failure: writes with `status='failed'` (and may retry via Celery backoff).

### `notify_alert(alert_id, remind_after_minutes=None)`

**File**: `server/app/workers/tasks/notification_tasks.py`

- Ensures the **alert** is `FIRING`.
- Derives `client_id` (from `alert` or related `machine`) and computes **cooldown**:
  - Override minutes (if provided) → seconds.
  - Else `get_remind_seconds(client_id)`.
- Looks up **last `success`** for this **alert_id** in `notification_log` and **skips** if inside cooldown.
- Enqueues `notify(payload)` to actually send Slack message.

### HTTP Monitor Service (`http_monitor_service.py`)

**File**: `server/app/application/services/http_monitor_service.py`

- Selects **active** HTTP targets and checks if **due** (`check_interval_seconds` elapsed since `last_check_at`).
- Performs the request via `http_get(...)` (safe wrapper; returns **0** status on transport errors).
- Updates `last_*` fields on the `http_targets` row.
- Opens or resolves **incidents** via `IncidentRepository`:
  - **Open** when status is **unexpected** (`0` or not equal in `accepted_status_codes`).
  - **Resolve** when OK again.
- **Incident notifications**:
  - For **new incidents** → notify immediately.
  - For **existing open incidents** → notify only if **cooldown** (per client) allows it, using last `success` for that **incident_id** in `notification_log`.

---

## Database Layer

### Tables of interest

- `client_settings`  
  - `client_id UUID PRIMARY KEY`  
  - `reminder_notification_seconds INT NULL` (per-client reminder)
- `notification_log`  
  - Tracks notifications. Key columns: `client_id`, `incident_id`, `alert_id`, `status`, `sent_at`, `created_at`.
- `incidents`  
  - One open incident per `(client_id, http_target_id)` ensured by unique constraint (e.g., `ux_incidents_open_by_target`).

### Effective reminder per client

Repository method:  
**File**: `server/app/infrastructure/persistence/repositories/client_settings_repository.py`

```python
def get_effective_reminder_seconds(self, client_id: UUID) -> int:
    # 1) client_settings.reminder_notification_seconds (>0)
    # 2) settings.ALERT_REMINDER_MINUTES (minutes -> seconds)
    # 3) default = 30min (1800s)
```
*(Ensure your implementation returns **seconds**.)*

### Incident de-duplication & unique constraint

- A unique index like `ux_incidents_open_by_target` prevents multiple **open** incidents for the same `(client_id, http_target_id)`.
- If a new failure is detected while the incident is still open, the INSERT tries and **fails** with `duplicate key` error; this is **expected** and means:
  - The system will keep the **existing** open incident.
  - You may still get **reminder notifications** based on cooldown and logs in `notification_log`.

Useful queries:
```sql
-- Open incidents
SELECT client_id, http_target_id, status, created_at, updated_at
FROM incidents
WHERE status = 'open'
ORDER BY updated_at DESC;

-- Effective reminder for a given client
SELECT client_id, reminder_notification_seconds
FROM client_settings
WHERE client_id = '551ade67-ec05-483e-8e19-981550244a4d';

-- Last successful notification events (per incident)
WITH last AS (
  SELECT incident_id, MAX(sent_at) AS last_sent
  FROM notification_log
  WHERE status='success' AND provider='slack'
  GROUP BY incident_id
)
SELECT incident_id, NOW() AT TIME ZONE 'UTC' AS now_utc, last_sent,
       EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE 'UTC') - last_sent))::int AS age_sec
FROM last
ORDER BY now_utc DESC;
```

---

## Configuration: Where Reminder Is Declared

**Places it can be defined/overridden:**
1. **Database** (highest priority)  
   - `client_settings.reminder_notification_seconds` (seconds; per client).
2. **Environment** (fallback)  
   - `ALERT_REMINDER_MINUTES` (minutes), commonly set in:
     - `.env.docker` / `.env.example`
     - `docker/docker-compose.yml` (`environment:` section for `api`, `worker`, `beat`)
     - CI (`.github/workflows/ci.yml`) and test configs (`pytest.ini`, test conftest)
3. **Code hard default**  
   - 30 minutes = 1800 seconds (when neither DB nor ENV is reliable).

> Rule of thumb: **DB wins**. If you see `600` seconds reported, it came from DB unless an explicit override was passed into a task.

---

## Diagnosis Playbook — Docker/Compose Commands

> Ensure you run these from the project root, or adjust `-f` paths accordingly.

### Check effective ENV across services
```bash
docker compose -f docker/docker-compose.yml exec -T api    env | grep DEFAULT_ALERT_REMINDER_MINUTES
docker compose -f docker/docker-compose.yml exec -T worker env | grep DEFAULT_ALERT_REMINDER_MINUTES
docker compose -f docker/docker-compose.yml exec -T beat   env | grep DEFAULT_ALERT_REMINDER_MINUTES
```

### Check effective reminder **from DB**
```bash
docker compose -f docker/docker-compose.yml exec -T api python - <<'PY'
from uuid import UUID
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
cid = UUID("551ade67-ec05-483e-8e19-981550244a4d")
with open_session() as s:
    print("effective_seconds(api)   =", ClientSettingsRepository(s).get_effective_reminder_seconds(cid))
PY

docker compose -f docker/docker-compose.yml exec -T worker python - <<'PY'
from uuid import UUID
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
cid = UUID("551ade67-ec05-483e-8e19-981550244a4d")
with open_session() as s:
    print("effective_seconds(worker)=", ClientSettingsRepository(s).get_effective_reminder_seconds(cid))
PY
```

### Check global helper `get_remind_seconds`
```bash
docker compose -f docker/docker-compose.yml exec -T api python - <<'PY'
from uuid import UUID
from app.workers.tasks.notification_tasks import get_remind_seconds
print("get_remind_seconds(api)   =", get_remind_seconds(UUID("551ade67-ec05-483e-8e19-981550244a4d")))
PY

docker compose -f docker/docker-compose.yml exec -T worker python - <<'PY'
from uuid import UUID
from app.workers.tasks.notification_tasks import get_remind_seconds
print("get_remind_seconds(worker)=", get_remind_seconds(UUID("551ade67-ec05-483e-8e19-981550244a4d")))
PY
```

### Inspect recent notification logs
```bash
# Last 30 logs (provider/recipient help identify what got sent)
docker compose -f docker/docker-compose.yml exec -T db psql -U postgres -d monitoring -c "
SELECT id, incident_id, status, provider, recipient, sent_at, created_at
FROM notification_log
ORDER BY created_at DESC
LIMIT 30;"

# Success vs pending (last 2h)
docker compose -f docker/docker-compose.yml exec -T db psql -U postgres -d monitoring -c "
SELECT
  COUNT(*) FILTER (WHERE sent_at IS NULL)      AS sent_at_null,
  COUNT(*) FILTER (WHERE sent_at IS NOT NULL)  AS sent_at_set
FROM notification_log
WHERE COALESCE(created_at, NOW()) > NOW() - INTERVAL '2 hours';"
```

### Check beat schedule & Celery queues
```bash
# What beat is scheduling
docker compose -f docker/docker-compose.yml exec -T beat python - <<'PY'
from app.workers.celery_app import celery
import pprint
pprint.pprint(celery.conf.beat_schedule)
PY

# Queues (from docker-compose.yml)
# worker starts with: -Q ingest,evaluate,heartbeat,http,notify,outbox
```

### Inspect incidents & duplicates
```bash
# Open incidents (verify only one per (client_id, http_target_id))
docker compose -f docker/docker-compose.yml exec -T db psql -U postgres -d monitoring -c "
SELECT client_id, http_target_id, status, created_at, updated_at
FROM incidents
WHERE status='open'
ORDER BY updated_at DESC;"

# You may see duplicate-key errors in DB logs when trying to open an already-open incident.
# This is expected: the unique constraint prevents duplicates and the system carries on.
```

---

## Adjusting Behavior

### Change per-client reminder in DB
```sql
UPDATE client_settings
SET reminder_notification_seconds = 1800  -- 30 minutes
WHERE client_id = '551ade67-ec05-483e-8e19-981550244a4d';
```

### Change global fallback in ENV
Update these locations (examples):
- `.env.docker` / `.env.example`:
  ```env
  DEFAULT_ALERT_REMINDER_MINUTES=30
  ```
- `docker/docker-compose.yml` under `environment` for `api`, `worker`, `beat`:
  ```yaml
  environment:
    DEFAULT_ALERT_REMINDER_MINUTES: "30"
  ```
- CI & tests may override with `1` minute for speed:
  - `.github/workflows/ci.yml`
  - `pytest.ini` and test conftest files

### When to restart services
- Any time you change **ENV** or code used at import, recreate services:
```bash
docker compose -f docker/docker-compose.yml up -d --force-recreate api worker beat
```
- DB changes (to `client_settings`) are immediate; **no restart** required.

---

## FAQ / Troubleshooting

**Q: I keep seeing `duplicate key value violates unique constraint "ux_incidents_open_by_target"` in DB logs. Is it bad?**  
A: It’s expected. It means an incident for the same `(client_id, http_target_id)` is already **open**. The system won’t create a duplicate; the existing incident remains, and reminders may still be sent based on cooldown.

**Q: My Slack reminders are every ~12 minutes instead of 10. Why?**  
A: If the cooldown is **600 s (10 min)** and beat runs the check every **120 s (2 min)**, the observed cadence may align to ~10–12 min due to scheduling granularity.

**Q: ENV shows 30 minutes but I still get 10-minute reminders.**  
A: DB per-client value **wins**. Check `client_settings.reminder_notification_seconds` — if it’s `600`, you’ll get 10 min reminders regardless of ENV.

**Q: Where does the “600 seconds” come from?**  
A: From **DB**: `client_settings.reminder_notification_seconds = 600` for that client.

**Q: Can I log the source of the reminder (DB vs ENV)?**  
A: Yes, add a `logger.debug("reminder_seconds.source", extra=...)` in `get_remind_seconds` after you decide the value.

---

## Reference Snippets

### SQL
```sql
-- Show per-client reminder
SELECT client_id, reminder_notification_seconds
FROM client_settings;

-- Update a specific client to 30 minutes (1800s)
UPDATE client_settings
SET reminder_notification_seconds = 1800
WHERE client_id = '551ade67-ec05-483e-8e19-981550244a4d';

-- Verify last success for specific incidents
WITH last AS (
  SELECT incident_id, MAX(sent_at) AS last_sent
  FROM notification_log
  WHERE status='success' AND provider='slack'
    AND incident_id IN ('830c4575-0705-42b4-af12-4571096b7aa1','368b3462-9803-42c9-a93b-fd0d9924ba52')
  GROUP BY incident_id
)
SELECT incident_id,
       NOW() AT TIME ZONE 'UTC' AS now_utc,
       last_sent,
       EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE 'UTC') - last_sent))::int AS age_sec
FROM last;
```

### Python (logging the source)
```python
def get_remind_seconds(client_id: str | uuid.UUID | None) -> int:
    DEFAULT_SECONDS = 30 * 60
    def _env_seconds() -> int:
        try:
            minutes = int(getattr(settings, "DEFAULT_ALERT_REMINDER_MINUTES", 30))
            return max(1, minutes) * 60
        except Exception:
            return DEFAULT_SECONDS

    if not client_id:
        secs = _env_seconds()
        logger.debug("reminder_seconds.source", extra={"client_id": None, "seconds": secs, "source": "env"})
        return secs

    try:
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
    except Exception:
        secs = _env_seconds()
        logger.debug("reminder_seconds.source", extra={"client_id": str(client_id), "seconds": secs, "source": "env"})
        return secs

    try:
        with open_session() as s:
            repo = ClientSettingsRepository(s)
            secs = int(repo.get_effective_reminder_seconds(cid))
            logger.debug("reminder_seconds.source", extra={"client_id": str(cid), "seconds": secs, "source": "db"})
            return secs
    except Exception:
        secs = _env_seconds()
        logger.debug("reminder_seconds.source", extra={"client_id": str(cid), "seconds": secs, "source": "env"})
        return secs
``

---

_Authors: Engineering_
