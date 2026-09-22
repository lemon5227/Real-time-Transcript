"""Process-wide logging so a classroom failure leaves evidence behind.

Before this module existed the backend logged nothing: transcription quality
problems, audio drops and provider errors only travelled over Socket.IO to the
browser, so a bad live transcript left no trace to inspect afterwards.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOGGER_NAME = "realtime_transcript"
DEFAULT_LOG_FILENAME = "realtime-transcript.log"
DEFAULT_LEVEL = logging.INFO
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False
_file_handler: Optional[RotatingFileHandler] = None


def get_logger(name: str = "") -> logging.Logger:
    """Return a namespaced logger without configuring handlers."""
    return logging.getLogger(LOGGER_NAME if not name else "%s.%s" % (LOGGER_NAME, name))


def default_log_directory() -> Path:
    """Where a real run should keep its log file.

    ``TRANSCRIPT_LOG_DIR`` wins so a launcher can redirect it. The packaged
    macOS app exports ``TRANSCRIPT_RUNTIME_DIR``, which keeps the log next to its
    runtime data. A source checkout falls back to ``logs/`` beside the code.
    """
    override = os.environ.get("TRANSCRIPT_LOG_DIR")
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("TRANSCRIPT_RUNTIME_DIR")
    if runtime:
        return Path(runtime).expanduser() / "logs"
    return Path(__file__).resolve().parents[1] / "logs"


def configure_logging(
    *,
    level: int = DEFAULT_LEVEL,
    log_directory: Optional[Path] = None,
) -> logging.Logger:
    """Attach a console handler, and a rotating file handler when asked.

    Safe to call more than once: a second call with a ``log_directory`` adds the
    file handler that the first call omitted, and never duplicates handlers.
    """
    global _configured, _file_handler

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    if not _configured:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)
        _configured = True

    if log_directory is not None and _file_handler is None:
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_directory / DEFAULT_LOG_FILENAME,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - unwritable log location
            logger.warning("日志文件不可写，仅输出到控制台：%s", exc)
        else:
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            _file_handler = handler

    return logger


def reset_logging_for_tests() -> None:
    """Detach handlers so a test can assert on a clean logger."""
    global _configured, _file_handler

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _configured = False
    _file_handler = None
