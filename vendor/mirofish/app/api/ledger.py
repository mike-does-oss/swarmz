"""
Ledger API路由
提供Agent性能跟踪、排行榜、模拟结果解析等接口
"""

import traceback
from flask import request, jsonify

from . import ledger_bp
from ..services.agent_ledger import AgentLedger
from ..services.outcome_resolver import OutcomeResolver
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.ledger')


# ============== Agent 列表接口 ==============

@ledger_bp.route('/agents', methods=['GET'])
def list_agents():
    """
    获取所有Agent及其统计信息

    Query参数：
        limit: 返回数量限制（可选，默认100）

    返回：
        {
            "success": true,
            "data": [
                {
                    "agent_id": "...",
                    "persona": "...",
                    "overall_stats": {...},
                    "simulations_count": 10,
                    "created_at": "...",
                    "updated_at": "..."
                }
            ],
            "count": 50
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)

        ledger = AgentLedger()
        agents = ledger.get_all_agents()

        # Limit and format response
        agents = agents[:limit]
        data = []

        for agent in agents:
            data.append({
                "agent_id": agent.agent_id,
                "persona": agent.persona,
                "overall_stats": agent.overall_stats.to_dict(),
                "simulations_count": len(agent.simulations),
                "created_at": agent.created_at,
                "updated_at": agent.updated_at
            })

        return jsonify({
            "success": True,
            "data": data,
            "count": len(data)
        })

    except Exception as e:
        logger.error(f"获取Agent列表失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@ledger_bp.route('/agents/<agent_id>', methods=['GET'])
def get_agent_detail(agent_id: str):
    """
    获取单个Agent的详细信息和完整历史

    返回：
        {
            "success": true,
            "data": {
                "agent_id": "...",
                "persona": "...",
                "overall_stats": {...},
                "simulations": [
                    {
                        "simulation_id": "...",
                        "question": "...",
                        "prediction": "...",
                        "predicted_outcome": "bullish",
                        "confidence": 0.75,
                        "actual_outcome": "bullish",
                        "correct": true,
                        "scored_at": "...",
                        "created_at": "..."
                    }
                ],
                "created_at": "...",
                "updated_at": "..."
            }
        }
    """
    try:
        ledger = AgentLedger()
        agent = ledger.get_agent_stats(agent_id)

        if not agent:
            return jsonify({
                "success": False,
                "error": f"Agent不存在: {agent_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": agent.to_dict()
        })

    except Exception as e:
        logger.error(f"获取Agent详情失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 排行榜接口 ==============

@ledger_bp.route('/top', methods=['GET'])
def get_top_agents():
    """
    获取表现最佳的Agent列表

    Query参数：
        n: 返回数量（可选，默认10）
        topic: 按主题过滤（可选，如 "macro", "rates", "equities"）

    返回：
        {
            "success": true,
            "data": [
                {
                    "rank": 1,
                    "agent_id": "...",
                    "persona": "...",
                    "hit_rate": 0.72,
                    "calibration_score": 0.15,
                    "alpha_score": 0.22,
                    "total_simulations": 25,
                    "best_topics": ["macro", "rates"]
                }
            ],
            "count": 10
        }
    """
    try:
        n = request.args.get('n', 10, type=int)
        topic = request.args.get('topic')

        ledger = AgentLedger()
        top_agents = ledger.get_top_agents(n=n, topic=topic)

        data = []
        for rank, agent in enumerate(top_agents, 1):
            # Get best topics (top 3 by hit rate)
            specializations = agent.overall_stats.specializations
            best_topics = sorted(
                specializations.items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]

            data.append({
                "rank": rank,
                "agent_id": agent.agent_id,
                "persona": agent.persona,
                "hit_rate": round(agent.overall_stats.hit_rate, 4),
                "calibration_score": round(agent.overall_stats.calibration_score, 4),
                "alpha_score": round(agent.overall_stats.alpha_score, 4),
                "total_simulations": agent.overall_stats.total_simulations,
                "predictions_made": agent.overall_stats.predictions_made,
                "correct": agent.overall_stats.correct,
                "avg_confidence": round(agent.overall_stats.avg_confidence, 4),
                "best_topics": [t[0] for t in best_topics]
            })

        return jsonify({
            "success": True,
            "data": data,
            "count": len(data),
            "topic_filter": topic
        })

    except Exception as e:
        logger.error(f"获取排行榜失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 模拟解析接口 ==============

@ledger_bp.route('/resolve', methods=['POST'])
def resolve_simulation():
    """
    解析模拟结果，为所有参与的Agent打分

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",      // 必填，模拟ID
            "actual_outcome": "bullish",      // 必填，实际结果
            "notes": "..."                    // 可选，备注
        }

    返回：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "actual_outcome": "bullish",
                "notes": "...",
                "resolved_at": "...",
                "agents_scored": 5,
                "agent_updates": [
                    {
                        "agent_id": "...",
                        "persona": "...",
                        "predicted_outcome": "bullish",
                        "correct": true,
                        "updated_stats": {
                            "hit_rate": 0.72,
                            "alpha_score": 0.22,
                            ...
                        }
                    }
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        actual_outcome = data.get('actual_outcome')
        notes = data.get('notes', '')

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        if not actual_outcome:
            return jsonify({
                "success": False,
                "error": "请提供 actual_outcome"
            }), 400

        resolver = OutcomeResolver()
        result = resolver.resolve_simulation(
            simulation_id=simulation_id,
            actual_outcome=actual_outcome,
            notes=notes
        )

        return jsonify({
            "success": True,
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:
        logger.error(f"解析模拟结果失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 模拟列表接口 ==============

@ledger_bp.route('/simulations', methods=['GET'])
def list_simulations():
    """
    获取所有模拟及其预测/结果状态

    Query参数：
        status: 过滤状态（可选，"pending" 或 "scored"）
        limit: 返回数量限制（可选，默认50）

    返回：
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "question": "...",
                    "topic_tags": ["macro", "rates"],
                    "created_at": "...",
                    "actual_outcome": "bullish" | null,
                    "scored": true | false,
                    "scored_at": "..." | null,
                    "agent_count": 5,
                    "predictions": [
                        {
                            "agent_id": "...",
                            "predicted_outcome": "bullish",
                            "confidence": 0.75,
                            "correct": true | null
                        }
                    ]
                }
            ],
            "count": 25
        }
    """
    try:
        status_filter = request.args.get('status')
        limit = request.args.get('limit', 50, type=int)

        ledger = AgentLedger()
        simulations = ledger.get_all_simulations()

        # Filter by status if specified
        if status_filter == 'pending':
            simulations = [s for s in simulations if not s.get('scored')]
        elif status_filter == 'scored':
            simulations = [s for s in simulations if s.get('scored')]

        # Limit results
        simulations = simulations[:limit]

        return jsonify({
            "success": True,
            "data": simulations,
            "count": len(simulations)
        })

    except Exception as e:
        logger.error(f"获取模拟列表失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent权重接口 ==============

@ledger_bp.route('/weights', methods=['GET'])
def get_agent_weights():
    """
    获取Agent权重（用于ReportAgent加权）

    Query参数：
        topic: 按主题计算权重（可选）

    返回：
        {
            "success": true,
            "data": {
                "agent_001": 0.08,
                "agent_002": 0.12,
                ...
            },
            "topic": "macro" | null
        }
    """
    try:
        topic = request.args.get('topic')

        ledger = AgentLedger()
        weights = ledger.get_weighted_agents(topic=topic)

        return jsonify({
            "success": True,
            "data": weights,
            "topic": topic
        })

    except Exception as e:
        logger.error(f"获取Agent权重失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 统计概览接口 ==============

@ledger_bp.route('/stats', methods=['GET'])
def get_ledger_stats():
    """
    获取Ledger统计概览

    返回：
        {
            "success": true,
            "data": {
                "total_agents": 50,
                "total_simulations": 25,
                "scored_simulations": 18,
                "pending_simulations": 7,
                "avg_hit_rate": 0.62,
                "avg_calibration": 0.18,
                "top_agent": {
                    "agent_id": "...",
                    "alpha_score": 0.35
                }
            }
        }
    """
    try:
        ledger = AgentLedger()
        agents = ledger.get_all_agents()
        simulations = ledger.get_all_simulations()

        total_agents = len(agents)
        total_simulations = len(simulations)
        scored_simulations = len([s for s in simulations if s.get('scored')])
        pending_simulations = total_simulations - scored_simulations

        # Calculate averages
        if agents:
            scored_agents = [a for a in agents if a.overall_stats.predictions_made > 0]
            if scored_agents:
                avg_hit_rate = sum(a.overall_stats.hit_rate for a in scored_agents) / len(scored_agents)
                avg_calibration = sum(a.overall_stats.calibration_score for a in scored_agents) / len(scored_agents)
            else:
                avg_hit_rate = 0.0
                avg_calibration = 0.0
        else:
            avg_hit_rate = 0.0
            avg_calibration = 0.0

        # Get top agent
        top_agents = ledger.get_top_agents(n=1)
        top_agent = None
        if top_agents:
            top_agent = {
                "agent_id": top_agents[0].agent_id,
                "alpha_score": round(top_agents[0].overall_stats.alpha_score, 4)
            }

        return jsonify({
            "success": True,
            "data": {
                "total_agents": total_agents,
                "total_simulations": total_simulations,
                "scored_simulations": scored_simulations,
                "pending_simulations": pending_simulations,
                "avg_hit_rate": round(avg_hit_rate, 4),
                "avg_calibration": round(avg_calibration, 4),
                "top_agent": top_agent
            }
        })

    except Exception as e:
        logger.error(f"获取Ledger统计失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
