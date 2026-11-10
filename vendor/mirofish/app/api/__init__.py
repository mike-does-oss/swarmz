"""
API Routes Module
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)
ledger_bp = Blueprint('ledger', __name__)
brief_bp = Blueprint('brief', __name__)
kalshi_bp = Blueprint('kalshi', __name__)
brief_v2_bp = Blueprint('brief_v2', __name__)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import ledger  # noqa: E402, F401
from . import brief  # noqa: E402, F401
from . import kalshi  # noqa: E402, F401
from . import brief_v2  # noqa: E402, F401

