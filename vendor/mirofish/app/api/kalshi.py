"""
Kalshi API Routes
Read-only proxy over Kalshi public market data for the markets dashboard.
"""

import requests
from flask import request, jsonify

from . import kalshi_bp
from ..services.kalshi_client import KalshiClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.kalshi')


@kalshi_bp.route('/markets', methods=['GET'])
def list_markets():
    try:
        status = request.args.get('status', 'open')
        limit = max(1, min(request.args.get('limit', 100, type=int), 1000))
        cursor = request.args.get('cursor')
        series_ticker = request.args.get('series_ticker')

        client = KalshiClient()

        # Default dashboard view: curated liquid single-outcome markets. Kalshi's
        # raw open feed is almost entirely illiquid KXMVE parlay combos with no
        # price. An explicit series_ticker or cursor opts back into the raw feed.
        if not series_ticker and not cursor:
            markets = client.list_featured_markets(limit=limit)
            return jsonify({
                "success": True,
                "data": {"markets": markets, "cursor": None},
            })

        markets, next_cursor = client.list_markets(
            status=status, limit=limit, cursor=cursor, series_ticker=series_ticker
        )
        return jsonify({
            "success": True,
            "data": {"markets": markets, "cursor": next_cursor},
        })
    except Exception as e:
        logger.error(f"Failed to list Kalshi markets: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@kalshi_bp.route('/market/<ticker>', methods=['GET'])
def get_market(ticker: str):
    try:
        client = KalshiClient()
        market = client.get_market(ticker)
        if not market or not market.get('ticker'):
            return jsonify({"success": False, "error": f"Market not found: {ticker}"}), 404
        return jsonify({"success": True, "data": market})
    except requests.HTTPError as e:
        status = getattr(e.response, 'status_code', None)
        if status == 404:
            return jsonify({"success": False, "error": f"Market not found: {ticker}"}), 404
        logger.error(f"Upstream error getting Kalshi market {ticker}: {e}")
        return jsonify({"success": False, "error": str(e)}), 502
    except Exception as e:
        logger.error(f"Failed to get Kalshi market {ticker}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
