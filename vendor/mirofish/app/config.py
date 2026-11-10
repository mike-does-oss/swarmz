"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

# swarmz: when embedded in swarmz the process environment must win over a .env
# file (upstream used override=True). Set MIROFISH_DOTENV_OVERRIDE=1 for the old behaviour.
_dotenv_override = os.environ.get('MIROFISH_DOTENV_OVERRIDE', '0') == '1'
if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=_dotenv_override)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=_dotenv_override)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')

    # Two-model split for the brief builder (spec §3): assembler and scrubber
    # must be different models in production (never grade your own homework).
    ASSEMBLER_MODEL = os.environ.get('ASSEMBLER_MODEL', LLM_MODEL_NAME)
    ASSEMBLER_BASE_URL = os.environ.get('ASSEMBLER_BASE_URL', LLM_BASE_URL)
    ASSEMBLER_API_KEY = os.environ.get('ASSEMBLER_API_KEY', LLM_API_KEY)
    SCRUBBER_MODEL = os.environ.get('SCRUBBER_MODEL', LLM_MODEL_NAME)
    SCRUBBER_BASE_URL = os.environ.get('SCRUBBER_BASE_URL', LLM_BASE_URL)
    SCRUBBER_API_KEY = os.environ.get('SCRUBBER_API_KEY', LLM_API_KEY)

    # Zep配置 — swarmz: Zep Cloud is no longer used (memory is Graphiti on FalkorDB via
    # app/memory_compat.py). Kept as a non-empty placeholder so legacy `if not ZEP_API_KEY`
    # guards in services/api routes pass; the value is never sent anywhere.
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY') or 'local-memory'

    # Kalshi public market-data API (no auth required for GET market data)
    KALSHI_API_BASE = os.environ.get('KALSHI_API_BASE', 'https://external-api.kalshi.com/trade-api/v2')

    # Brave Search API (retrieval)
    BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY', '')

    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    # UPLOAD_FOLDER / DATA_DIR are env-overridable so a hosted deploy (e.g. Render)
    # can point them at a persistent disk; default to the repo-local dirs for dev.
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # BriefBuilder v2 storage paths (data root: backend/data/ by default)
    DATA_DIR = os.path.abspath(os.environ.get('DATA_DIR') or os.path.join(os.path.dirname(__file__), '../data'))
    SHOALS_DB_PATH = os.path.join(DATA_DIR, 'shoals.db')
    BRIEFS_V2_DIR = os.path.join(DATA_DIR, 'briefs_v2')
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.environ.get('OASIS_SIMULATION_DATA_DIR') or os.path.join(UPLOAD_FOLDER, 'simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        return errors

