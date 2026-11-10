import pytest

from app.config import Config
from app.services.brief_v2 import stage_llm


def test_models_are_distinct_true_when_models_differ(monkeypatch):
    monkeypatch.setattr(Config, "ASSEMBLER_MODEL", "model-a")
    monkeypatch.setattr(Config, "ASSEMBLER_BASE_URL", "https://a")
    monkeypatch.setattr(Config, "SCRUBBER_MODEL", "model-b")
    monkeypatch.setattr(Config, "SCRUBBER_BASE_URL", "https://a")
    assert stage_llm.models_are_distinct() is True


def test_models_are_distinct_false_when_identical(monkeypatch):
    monkeypatch.setattr(Config, "ASSEMBLER_MODEL", "same")
    monkeypatch.setattr(Config, "ASSEMBLER_BASE_URL", "https://x")
    monkeypatch.setattr(Config, "SCRUBBER_MODEL", "same")
    monkeypatch.setattr(Config, "SCRUBBER_BASE_URL", "https://x")
    assert stage_llm.models_are_distinct() is False


def test_warn_if_same_model_logs_when_identical(monkeypatch, caplog):
    import logging
    # Temporarily enable propagation for all loggers (scoped to this test only)
    loggers = {}
    for name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(name)
        if isinstance(logger, logging.Logger):
            loggers[name] = logger.propagate
            logger.propagate = True

    try:
        monkeypatch.setattr(Config, "ASSEMBLER_MODEL", "same")
        monkeypatch.setattr(Config, "ASSEMBLER_BASE_URL", "https://x")
        monkeypatch.setattr(Config, "SCRUBBER_MODEL", "same")
        monkeypatch.setattr(Config, "SCRUBBER_BASE_URL", "https://x")
        with caplog.at_level(logging.WARNING):
            stage_llm.warn_if_same_model()
        assert any("same model" in r.message.lower() or "grade" in r.message.lower()
                   for r in caplog.records)
    finally:
        # Restore original propagate settings
        for name, propagate in loggers.items():
            logging.getLogger(name).propagate = propagate


def test_factories_build_clients_with_stage_config(monkeypatch):
    monkeypatch.setattr(Config, "ASSEMBLER_API_KEY", "key-a")
    monkeypatch.setattr(Config, "ASSEMBLER_BASE_URL", "https://a/v1")
    monkeypatch.setattr(Config, "ASSEMBLER_MODEL", "model-a")
    monkeypatch.setattr(Config, "SCRUBBER_API_KEY", "key-b")
    monkeypatch.setattr(Config, "SCRUBBER_BASE_URL", "https://b/v1")
    monkeypatch.setattr(Config, "SCRUBBER_MODEL", "model-b")
    a = stage_llm.assembler_llm()
    s = stage_llm.scrubber_llm()
    assert a.model == "model-a" and a.base_url == "https://a/v1"
    assert s.model == "model-b" and s.base_url == "https://b/v1"
