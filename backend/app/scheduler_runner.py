"""The suite's exactly-one supervised scheduler process."""

import os

os.environ.setdefault("APP_PROCESS_ROLE", "scheduler")

from app import database, scheduler  # noqa: E402
from app.runtime_logging import configure_runtime_logging  # noqa: E402


def main() -> None:
    configure_runtime_logging("scheduler")
    database.init_db()
    scheduler.run_forever()


if __name__ == "__main__":
    main()
