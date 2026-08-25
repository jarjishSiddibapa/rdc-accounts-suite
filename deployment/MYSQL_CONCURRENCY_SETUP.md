# MySQL concurrency setup

Run this once after pulling the concurrency release on production:

Run this from **Command Prompt** in the project root:

```bat
mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_durable_concurrency.sql
```

From PowerShell, wrap the same command with `cmd /c`:

```powershell
cmd /c "mysql -h <MYSQL_HOST> -P <MYSQL_PORT> -u <MYSQL_USER> -p < deployment\mysql\20260825_durable_concurrency.sql"
```

The SQL is idempotent. It creates only new runtime tables and the initial
global Oracle/GST capacity slot; it does not delete or overwrite existing
users, mappings, settings, reports, or audit rows.

If `MYSQL_DATABASE` is not `rdc_accounts_suite`, edit the `CREATE DATABASE`
and `USE` lines in the SQL file before running it.

After applying it, start the deployment using `start_all.bat`. The application
also runs idempotent schema verification while starting, guarded by the MySQL
named lock `<MYSQL_DATABASE>:schema-init`, so simultaneous process startup
cannot race the schema or seed operations.

Default process settings can be overridden in `backend/.env`:

```dotenv
API_WORKERS=2
JOB_WORKER_PROCESSES=2
JOB_LEASE_SECONDS=120
JOB_HEARTBEAT_SECONDS=15
JOB_MAX_ATTEMPTS=2
ORACLE_GST_JOB_CONCURRENCY=1
```

For the documented 4-core/8-GB production host, keep the defaults until a
representative load test shows enough RAM and Oracle capacity for more.
