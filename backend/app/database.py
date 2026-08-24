"""SQLAlchemy engine/session setup for the suite's single shared MySQL
database. Every table used across the whole suite (users, per-user email
settings, and every tool's mapping tables) lives in this one MySQL database
so mapping data is safe under concurrent LAN access and isn't scattered
across per-desktop files anymore."""

from urllib.parse import quote_plus

import pymysql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app import config


def _ensure_database_exists() -> None:
    """Create the target MySQL database if it doesn't exist yet (first run).

    Uses utf8mb4_bin (exact byte comparison), not a *_ci Unicode collation:
    the mapping tables need uniqueness to match Python's exact string
    equality (the app already does its own case-insensitive lookups via
    .upper() in the service layer). A _ci collation's Unicode-aware
    comparison can treat visually-similar-but-different characters (e.g.
    a non-breaking space vs a regular space inside a vendor site code) as
    equal, which silently breaks unique constraints on real business data
    that Python itself would never consider a duplicate.
    """
    conn = pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{config.MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        conn.commit()
    finally:
        conn.close()


_ensure_database_exists()

_DATABASE_URL = (
    f"mysql+pymysql://{quote_plus(config.MYSQL_USER)}:{quote_plus(config.MYSQL_PASSWORD)}"
    f"@{config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}?charset=utf8mb4"
)

engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=600,
    pool_size=config.DB_POOL_SIZE,
    max_overflow=config.DB_MAX_OVERFLOW,
    pool_timeout=config.DB_POOL_TIMEOUT_SECONDS,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session, always closed afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _apply_additive_schema_updates() -> None:
    """Add non-destructive columns/constraints missing from older installs."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables or "is_deleted" not in table.c:
                continue
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            if "is_deleted" not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE `{table.name}` ADD COLUMN `is_deleted` "
                        "BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )

    # These user-management fields predate the migration helper and may be
    # absent on installations upgraded from the original desktop port.
    # Existing accounts remain active. Application access now fails closed:
    # a missing/legacy NULL allowed_apps value grants a regular user nothing.
    inspector = inspect(engine)
    if "users" in existing_tables:
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        with engine.begin() as connection:
            if "is_active" not in user_columns:
                connection.execute(
                    text(
                        "ALTER TABLE `users` ADD COLUMN `is_active` "
                        "BOOLEAN NOT NULL DEFAULT TRUE"
                    )
                )
            if "allowed_apps" not in user_columns:
                connection.execute(
                    text("ALTER TABLE `users` ADD COLUMN `allowed_apps` TEXT NULL")
                )
            if "first_name" not in user_columns:
                connection.execute(
                    text("ALTER TABLE `users` ADD COLUMN `first_name` VARCHAR(100) NULL")
                )
            if "last_name" not in user_columns:
                connection.execute(
                    text("ALTER TABLE `users` ADD COLUMN `last_name` VARCHAR(100) NULL")
                )

    # applications.collaborator predates the migration helper too — installs
    # whose `applications` table was created before the footer-credit feature
    # existed won't have this column yet.
    if "applications" in existing_tables:
        application_columns = {column["name"] for column in inspector.get_columns("applications")}
        if "collaborator" not in application_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE `applications` ADD COLUMN `collaborator` VARCHAR(255) NULL")
                )

    # backup_settings.scratch_cleanup_hours predates the migration helper too.
    if "backup_settings" in existing_tables:
        backup_columns = {column["name"] for column in inspector.get_columns("backup_settings")}
        if "scratch_cleanup_hours" not in backup_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE `backup_settings` ADD COLUMN `scratch_cleanup_hours` "
                        "INT NOT NULL DEFAULT 6"
                    )
                )

    if "users" not in existing_tables:
        return

    inspector = inspect(engine)
    has_unique_email = any(
        constraint.get("column_names") == ["email"]
        for constraint in inspector.get_unique_constraints("users")
    )
    if has_unique_email:
        return

    with engine.begin() as connection:
        duplicate = connection.execute(
            text(
                "SELECT LOWER(email) AS normalized_email, COUNT(*) AS total "
                "FROM users GROUP BY LOWER(email) HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                "Cannot add the required unique user-email constraint because duplicate "
                f"accounts exist for {duplicate.normalized_email!r}. Resolve them without "
                "deleting history, then restart."
            )
        connection.execute(
            text("ALTER TABLE `users` ADD CONSTRAINT `uq_users_email` UNIQUE (`email`)")
        )


def init_db():
    """Create all tables (if missing) and seed initial data.

    `auth`/`system_mailer` are imported lazily here (rather than at module
    load) because they import models.py which imports this module —
    importing them eagerly at the top of database.py would create a
    circular import.
    """
    # Register every model on Base.metadata before create_all/migrations.
    # Importing shared models after migration would leave existing installs
    # without new shared columns such as users.is_deleted/is_active.
    from app import models as _shared_models  # noqa: F401
    from app.services.rdc_payables import models as _rdc_models  # noqa: F401
    from app.services.unaccounted import models as _unaccounted_models  # noqa: F401
    from app.services.trial_balance import models as _trial_balance_models  # noqa: F401
    from app.services.gstr2b import models as _gstr2b_models  # noqa: F401
    from app.services.unapplied_receipts import models as _unapplied_receipts_models  # noqa: F401
    from app.services.ultrafine_balance_confirmation import models as _ultrafine_bc_models  # noqa: F401
    from app.services.ultrafine_payment_reminder import models as _ultrafine_pr_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_additive_schema_updates()

    from app import auth, system_mailer  # lazy import to avoid circular import
    from app.permissions import seed_applications
    from app.services.rdc_payables import mapping_store as payables_mappings
    from app.services.unaccounted import mappings as unaccounted_mappings
    from app.services.trial_balance import mapping_store as trial_balance_mappings

    db = SessionLocal()
    try:
        auth.seed_initial_users(db)
        seed_applications(db)
        system_mailer.seed_system_email(db)
        # Add only legacy natural keys that have never existed. Existing and
        # soft-deleted mappings are intentionally left untouched so admin
        # edits/archives always take precedence over bundled seed data.
        payables_mappings.seed_missing_from_excel(db)
        unaccounted_mappings.seed_missing_from_json(db)
        trial_balance_mappings.seed_missing_from_excel(db)
    finally:
        db.close()
