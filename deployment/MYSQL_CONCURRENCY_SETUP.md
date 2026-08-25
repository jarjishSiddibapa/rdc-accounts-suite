# MySQL concurrency setup

The suite uses MySQL for durable jobs, cross-process leases, rate limits,
browser-tab ownership, and the shared Oracle/GST capacity gate. Apply the SQL
migrations before starting the updated production application.

## Which migration to run

For a new environment, run:

`deployment/mysql/20260825_durable_concurrency.sql`

The current version creates the five runtime tables, the initial `oracle-gst`
slot, and the browser-tab lease columns and indexes.

If production already ran an earlier copy of the durable-concurrency migration
before browser-tab ownership was added, also run:

`deployment/mysql/20260825_tab_owned_jobs.sql`

The second migration adds only these missing `background_jobs` fields:

- `client_tab_id`
- `client_heartbeat_at`
- `cancel_on_disconnect`

Both scripts are idempotent and safe to run more than once. They do not delete
or overwrite existing users, mappings, settings, reports, or audit rows. If
`MYSQL_DATABASE` is not `rdc_accounts_suite`, change the `CREATE DATABASE` and
`USE` statements before executing either script.

## MySQL Workbench

Open the applicable SQL file in MySQL Workbench and execute the complete
script. For an existing production database that already has the original
runtime tables, the additional script to execute is
`deployment/mysql/20260825_tab_owned_jobs.sql`.

## Command-line alternatives

From Command Prompt in the project root:

```bat
mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_durable_concurrency.sql
mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_tab_owned_jobs.sql
```

From PowerShell, pass input redirection through `cmd /c`:

```powershell
cmd /c "mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_durable_concurrency.sql"
cmd /c "mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_tab_owned_jobs.sql"
```

The second command is required only when upgrading a database that was created
from the earlier durable-concurrency script. Running both is safe.

## Verify the production schema

Run these queries in MySQL Workbench after applying the migration:

```sql
USE `rdc_accounts_suite`;

SELECT
  COLUMN_NAME,
  COLUMN_TYPE,
  IS_NULLABLE,
  COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'background_jobs'
  AND COLUMN_NAME IN (
    'client_tab_id',
    'client_heartbeat_at',
    'cancel_on_disconnect'
  )
ORDER BY COLUMN_NAME;

SELECT
  INDEX_NAME,
  COLUMN_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'background_jobs'
  AND INDEX_NAME IN (
    'ix_background_jobs_client_tab_id',
    'ix_background_jobs_client_heartbeat_at'
  )
ORDER BY INDEX_NAME;

SELECT
  resource_key,
  slot_number,
  job_id,
  lease_owner,
  lease_expires_at,
  is_deleted
FROM background_resource_slots
WHERE resource_key = 'oracle-gst'
ORDER BY slot_number;
```

The first query must return all three tab-lease columns, the second must return
both indexes, and the final query must return at least the active
`oracle-gst` slot.

## Runtime behavior and settings

API processes enqueue work and processing workers claim it through MySQL. A
browser-started job is owned by the physical tab through `X-Client-Tab-ID` and
a heartbeat stored in `background_jobs`. Closing or navigating away from a tab
abandons its cancellable work. The worker then stops the active processing,
releases any `oracle-gst` slot, and allows the next waiting job to run. A stale
heartbeat timeout is the fallback when the browser cannot send its explicit
abandon request.

Email dispatch is intentionally detached after sending begins because SMTP
delivery cannot be safely reversed midway through a batch.

Default process and lease settings can be overridden in `backend/.env`:

```dotenv
API_WORKERS=2
JOB_WORKER_PROCESSES=2
JOB_POLL_SECONDS=0.75
JOB_LEASE_SECONDS=120
JOB_HEARTBEAT_SECONDS=15
JOB_MAX_ATTEMPTS=2
JOB_CLIENT_MONITOR_SECONDS=2
JOB_CLIENT_HEARTBEAT_TIMEOUT_SECONDS=120
ORACLE_GST_JOB_CONCURRENCY=1
```

Increasing API or worker counts does not automatically multiply Oracle
capacity. GST Oracle jobs must still acquire a shared `oracle-gst` database
slot. Increase `ORACLE_GST_JOB_CONCURRENCY` only after measuring Oracle server
capacity, database-session limits, application-server memory, and representative
production workload.

For the documented 4-core/8-GB production host, keep the defaults until a load
test shows sufficient capacity for more.

## Production update sequence

1. Check for important active jobs and allow them to finish.
2. Pull the updated `main` branch with `git pull origin main`.
3. Apply the applicable SQL migration in MySQL Workbench.
4. Stop the old launcher cleanly.
5. Start `start_all.bat`.
6. Check `http://127.0.0.1:2805/api/health` and `backend/logs/`.
7. Start two GST jobs in separate tabs, close the running tab, and verify that
   its job stops, its Oracle slot is released, and the waiting job starts.

At startup, the application also performs idempotent schema verification under
the MySQL named lock `<MYSQL_DATABASE>:schema-init`, preventing simultaneous API
and worker processes from racing schema initialization or additive seeds.
