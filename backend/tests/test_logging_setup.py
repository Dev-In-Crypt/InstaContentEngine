"""setup_logging configures the root logger so app logs actually surface."""
import logging

from services.http_utils import setup_logging


def test_setup_logging_sets_root_level_and_handler():
    logging.getLogger().setLevel(logging.CRITICAL)   # start somewhere else
    setup_logging("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert root.handlers, "expected at least one handler on the root logger"


def test_setup_logging_bad_level_falls_back_to_info():
    setup_logging("NONSENSE")   # must not raise
    assert logging.getLogger().level == logging.INFO


def test_app_logger_emits_at_configured_level(caplog):
    setup_logging("INFO")
    with caplog.at_level(logging.INFO):
        logging.getLogger("services.scheduler").info("scheduled publish OK")
    assert any("scheduled publish OK" in r.message for r in caplog.records)
