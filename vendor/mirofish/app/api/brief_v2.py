"""
Brief v2 API Routes
Staged flow: build a frozen, contamination-audited brief from a Kalshi market.
(Prediction run — MiroFish + baseline — is added in Plan 7.)
"""

import threading
from datetime import datetime

from flask import request, jsonify

from . import brief_v2_bp
from ..config import Config
from ..services.kalshi_client import KalshiClient
from ..services.brief_v2.pipeline import BriefPipeline
from ..services.prediction.run_prediction import run_prediction
from ..services.acceptance.run_audit import audit_all
from ..storage.db import get_connection, list_predictions
from ..models.task import TaskManager
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.brief_v2')


@brief_v2_bp.route('/build', methods=['POST'])
def build_brief():
    try:
        data = request.get_json() or {}
        ticker = data.get('ticker')
        if not ticker:
            return jsonify({"success": False, "error": "Please provide 'ticker'"}), 400

        market = KalshiClient().get_market(ticker)
        if not market or not market.get('ticker'):
            return jsonify({"success": False, "error": f"Market not found: {ticker}"}), 404

        now = datetime.utcnow().isoformat() + 'Z'
        result = BriefPipeline().build(market, now=now)
        logger.info(f"brief v2 build for {ticker}: {result.status}")

        return jsonify({
            "success": True,
            "data": {
                "status": result.status,
                "brief_hash": result.brief_hash,
                "reject_reason": result.reject_reason,
                "word_count": result.word_count,
            },
        })
    except Exception as e:
        logger.error(f"brief v2 build failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _spawn_run_prediction(question_id: str, num_agents, max_rounds) -> str:
    task_manager = TaskManager()  # singleton; task is pollable via the existing task API
    task_id = task_manager.create_task(task_type="run_prediction",
                                       metadata={"question_id": question_id})

    def _worker():
        try:
            result = run_prediction(question_id, num_agents=num_agents, max_rounds=max_rounds)
            task_manager.complete_task(task_id, result=result.to_dict())
        except Exception as e:  # never crash the server on a background failure
            logger.error(f"run_prediction task failed for {question_id}: {e}")
            task_manager.fail_task(task_id, error=str(e))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return task_id


@brief_v2_bp.route('/<question_id>/run-prediction', methods=['POST'])
def run_prediction_route(question_id: str):
    try:
        data = request.get_json() or {}
        num_agents = data.get('num_agents')
        if num_agents is not None:
            if not isinstance(num_agents, int) or isinstance(num_agents, bool) \
                    or num_agents < 1 or num_agents > 5000:
                return jsonify({"success": False,
                                "error": "Invalid 'num_agents': must be an integer in [1, 5000]"}), 400
        max_rounds = data.get('max_rounds')
        if max_rounds is not None:
            if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) \
                    or max_rounds < 1 or max_rounds > 500:
                return jsonify({"success": False,
                                "error": "Invalid 'max_rounds': must be an integer in [1, 500]"}), 400

        task_id = _spawn_run_prediction(question_id, num_agents, max_rounds)
        return jsonify({
            "success": True,
            "data": {"question_id": question_id, "task_id": task_id, "status": "running"},
        })
    except Exception as e:
        logger.error(f"run-prediction failed to start for {question_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@brief_v2_bp.route('/predictions', methods=['GET'])
def list_predictions_route():
    try:
        category = request.args.get('category')
        conn = get_connection(Config.SHOALS_DB_PATH)
        try:
            rows = list_predictions(conn, category=category)
        finally:
            conn.close()
        return jsonify({
            "success": True,
            "data": {"predictions": [r.to_dict() for r in rows], "count": len(rows)},
        })
    except Exception as e:
        logger.error(f"failed to list predictions: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@brief_v2_bp.route('/audit', methods=['GET'])
def audit_route():
    try:
        run_llm = request.args.get('llm') == '1'
        llm = LLMClient() if run_llm else None
        conn = get_connection(Config.SHOALS_DB_PATH)
        try:
            report = audit_all(conn, Config.BRIEFS_V2_DIR, llm=llm)
        finally:
            conn.close()
        return jsonify({"success": True, "data": report})
    except Exception as e:
        logger.error(f"acceptance audit failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
