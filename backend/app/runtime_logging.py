"""Per-process rotating logs safe for a multi-process Windows deployment."""

import logging
import os
from logging.handlers import RotatingFileHandler

from app import config


def configure_runtime_logging(role: str) -> None:
    log_format = "%(asctime)s %(levelname)s %(name)s %(message)s"
    handler = RotatingFileHandler(
        config.LOGS_DIR / f"app-{role}-{os.getpid()}.log",
        maxBytes=10_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(log_format))
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[logging.StreamHandler(), handler],
        force=True,
    )
