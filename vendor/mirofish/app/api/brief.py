"""
Brief API Routes
Provides endpoints for research brief building, retrieval, and seed document conversion
"""

from flask import request, jsonify

from . import brief_bp
from ..services.brief_builder import BriefBuilder
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.brief')


# ============== Brief Building ==============

@brief_bp.route('/build', methods=['POST'])
def build_brief():
    """
    Start building a research brief (async)

    Request (JSON):
        {
            "topic": "Electric Vehicles Market",  // Required
            "depth": "standard"                   // Optional: 'quick' (3), 'standard' (6), 'deep' (10)
        }

    Returns:
        {
            "success": true,
            "data": {
                "brief_id": "brief_xxxx",
                "topic": "Electric Vehicles Market",
                "depth": "standard",
                "status": "pending",
                "message": "Brief building started"
            }
        }
    """
    try:
        data = request.get_json() or {}

        topic = data.get('topic')
        if not topic:
            return jsonify({
                "success": False,
                "error": "Please provide 'topic'"
            }), 400

        depth = data.get('depth', 'standard')
        if depth not in ['quick', 'standard', 'deep']:
            return jsonify({
                "success": False,
                "error": "Invalid depth. Must be 'quick', 'standard', or 'deep'"
            }), 400

        builder = BriefBuilder()
        brief_id = builder.build_async(topic, depth)

        logger.info(f"Brief building started: {brief_id} for topic: {topic}")

        return jsonify({
            "success": True,
            "data": {
                "brief_id": brief_id,
                "topic": topic,
                "depth": depth,
                "status": "pending",
                "message": "Brief building started"
            }
        })

    except Exception as e:
        logger.error(f"Failed to start brief building: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Brief Retrieval ==============

@brief_bp.route('/<brief_id>', methods=['GET'])
def get_brief(brief_id: str):
    """
    Get brief by ID

    Returns:
        {
            "success": true,
            "data": {
                "brief_id": "brief_xxxx",
                "topic": "...",
                "depth": "standard",
                "status": "completed",
                "created_at": "...",
                "updated_at": "...",
                "summary": "...",
                "sections": [
                    {
                        "title": "...",
                        "content": "...",
                        "sources": ["url1", "url2"]
                    }
                ],
                "raw_search_results": [...],
                "perplexity_insights": [...],
                "error_message": ""
            }
        }
    """
    try:
        builder = BriefBuilder()
        brief = builder.get_brief(brief_id)

        if not brief:
            return jsonify({
                "success": False,
                "error": f"Brief not found: {brief_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": brief.to_dict()
        })

    except Exception as e:
        logger.error(f"Failed to get brief {brief_id}: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Brief Listing ==============

@brief_bp.route('/list', methods=['GET'])
def list_briefs():
    """
    List all briefs with pagination

    Query params:
        - limit: Number of briefs to return (default: 20)
        - offset: Offset for pagination (default: 0)

    Returns:
        {
            "success": true,
            "data": {
                "briefs": [
                    {
                        "brief_id": "brief_xxxx",
                        "topic": "...",
                        "depth": "standard",
                        "status": "completed",
                        "created_at": "...",
                        "updated_at": "..."
                    }
                ],
                "count": 5,
                "limit": 20,
                "offset": 0
            }
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        # Cap limit at 100
        limit = min(limit, 100)

        builder = BriefBuilder()
        briefs = builder.list_briefs(limit=limit, offset=offset)

        return jsonify({
            "success": True,
            "data": {
                "briefs": briefs,
                "count": len(briefs),
                "limit": limit,
                "offset": offset
            }
        })

    except Exception as e:
        logger.error(f"Failed to list briefs: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Load as Seed Document ==============

@brief_bp.route('/<brief_id>/load-as-seed', methods=['POST'])
def load_as_seed(brief_id: str):
    """
    Convert a completed brief into a seed document for MiroFish simulation

    Returns:
        {
            "success": true,
            "data": {
                "brief_id": "brief_xxxx",
                "seed_document": "# Research Brief: ...\n\n## Executive Summary\n...",
                "format": "markdown"
            }
        }
    """
    try:
        builder = BriefBuilder()
        brief = builder.get_brief(brief_id)

        if not brief:
            return jsonify({
                "success": False,
                "error": f"Brief not found: {brief_id}"
            }), 404

        if brief.status != 'completed':
            return jsonify({
                "success": False,
                "error": f"Brief is not completed. Current status: {brief.status}"
            }), 400

        seed_document = builder.convert_to_seed_document(brief_id)

        if not seed_document:
            return jsonify({
                "success": False,
                "error": "Failed to convert brief to seed document"
            }), 500

        logger.info(f"Brief {brief_id} converted to seed document")

        return jsonify({
            "success": True,
            "data": {
                "brief_id": brief_id,
                "seed_document": seed_document,
                "format": "markdown"
            }
        })

    except Exception as e:
        logger.error(f"Failed to convert brief {brief_id} to seed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
