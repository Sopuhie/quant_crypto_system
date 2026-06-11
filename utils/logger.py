"""Rotating file logger with separate info and error log streams."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


class _MaxLevelFilter(logging.Filter):
    """Allow records up to (and including) a maximum level."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def setup_logging(
    log_dir: Optional[Path | str] = None,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    console: bool = True,
) -> None:
    """
    Configure root logging with rotating info.log and error.log handlers.

    - info.log: DEBUG, INFO, WARNING (routine execution metrics)
    - error.log: ERROR, CRITICAL (failures and severe issues)
    """
    global _initialized

    log_path = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    if _initialized:
        root.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    info_handler = RotatingFileHandler(
        log_path / "info.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.DEBUG)
    info_handler.addFilter(_MaxLevelFilter(logging.WARNING))
    info_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        log_path / "error.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    root.addHandler(info_handler)
    root.addHandler(error_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, initializing default handlers on first use."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
