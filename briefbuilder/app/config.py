"""Settings for the brief builder, read from the environment.

swarmz starts the builder as a child process and passes these in (see swarmz/picker.py): it never
reads a .env file itself, so the keys it uses are always the ones its caller chose.
"""

import os


class Config:
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')

    # The assembler writes the brief and the scrubber checks it. They should be different models.
    ASSEMBLER_MODEL = os.environ.get('ASSEMBLER_MODEL', LLM_MODEL_NAME)
    ASSEMBLER_BASE_URL = os.environ.get('ASSEMBLER_BASE_URL', LLM_BASE_URL)
    ASSEMBLER_API_KEY = os.environ.get('ASSEMBLER_API_KEY', LLM_API_KEY)
    SCRUBBER_MODEL = os.environ.get('SCRUBBER_MODEL', LLM_MODEL_NAME)
    SCRUBBER_BASE_URL = os.environ.get('SCRUBBER_BASE_URL', LLM_BASE_URL)
    SCRUBBER_API_KEY = os.environ.get('SCRUBBER_API_KEY', LLM_API_KEY)

    # "claude-code" sends model calls, or web searches, through Claude Code on a Claude subscription: no keys needed.
    LLM_BACKEND = os.environ.get('LLM_BACKEND', 'api')
    SEARCH_BACKEND = os.environ.get('SEARCH_BACKEND', 'brave')
    if LLM_BACKEND == 'claude-code':
        LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'claude-haiku-5-5')
        ASSEMBLER_MODEL = os.environ.get('ASSEMBLER_MODEL', 'claude-sonnet-5-5')
        SCRUBBER_MODEL = os.environ.get('SCRUBBER_MODEL', 'claude-haiku-5-5')

    KALSHI_API_BASE = os.environ.get('KALSHI_API_BASE', 'https://external-api.kalshi.com/trade-api/v2')
    BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY', '')

    # Frozen briefs and the builder's own log of what it built. Must be writable.
    DATA_DIR = os.path.abspath(os.environ.get('DATA_DIR') or os.path.join(os.path.dirname(__file__), '../data'))
    SHOALS_DB_PATH = os.path.join(DATA_DIR, 'shoals.db')
    BRIEFS_V2_DIR = os.path.join(DATA_DIR, 'briefs_v2')
