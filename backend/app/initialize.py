"""Run idempotent MySQL schema updates/seeds before services start."""

import os

os.environ.setdefault("APP_PROCESS_ROLE", "initializer")

from app import database  # noqa: E402
from app.runtime_logging import configure_runtime_logging  # noqa: E402


def main() -> None:
    configure_runtime_logging("initializer")
    database.init_db()


if __name__ == "__main__":
    main()
