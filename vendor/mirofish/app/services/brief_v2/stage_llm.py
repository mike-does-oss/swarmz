"""
Two-model stage LLM factory (spec §3).

The Assembler (model #1) and Scrubber (model #2) must be different models in
production so the builder never grades its own homework. Each stage resolves its
own model/base_url/api_key, defaulting to the shared LLM_* config; a warning is
logged if they collapse to the same model (acceptable only for dev).
"""

from ...config import Config
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.stage_llm')


def assembler_llm() -> LLMClient:
    return LLMClient(
        api_key=Config.ASSEMBLER_API_KEY,
        base_url=Config.ASSEMBLER_BASE_URL,
        model=Config.ASSEMBLER_MODEL,
    )


def scrubber_llm() -> LLMClient:
    return LLMClient(
        api_key=Config.SCRUBBER_API_KEY,
        base_url=Config.SCRUBBER_BASE_URL,
        model=Config.SCRUBBER_MODEL,
    )


def models_are_distinct() -> bool:
    return (Config.ASSEMBLER_BASE_URL, Config.ASSEMBLER_MODEL) != \
           (Config.SCRUBBER_BASE_URL, Config.SCRUBBER_MODEL)


def warn_if_same_model() -> None:
    if not models_are_distinct():
        logger.warning(
            "Assembler and Scrubber resolve to the SAME model (%s); spec §3 wants "
            "different models so the builder does not grade its own homework. "
            "Acceptable only for development.",
            Config.ASSEMBLER_MODEL,
        )
