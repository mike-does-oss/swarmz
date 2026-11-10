"""
Agent Performance Ledger
Persistent JSON-backed ledger tracking agent performance across simulations.

Features:
- Records predictions made by each agent
- Tracks actual outcomes and scores agents
- Computes hit rate, calibration score, alpha score
- Provides weighted agent rankings for ReportAgent
"""

import os
import json
import uuid
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.agent_ledger')

# Lock for thread-safe file operations
_ledger_lock = threading.Lock()


@dataclass
class SimulationRecord:
    """Record of a single simulation prediction by an agent"""
    simulation_id: str
    question: str
    topic_tags: List[str]
    prediction: str
    confidence: float  # 0-1
    predicted_outcome: str  # e.g., "bullish", "bearish", "neutral"
    actual_outcome: Optional[str] = None
    correct: Optional[bool] = None
    scored_at: Optional[str] = None
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimulationRecord':
        return cls(
            simulation_id=data.get('simulation_id', ''),
            question=data.get('question', ''),
            topic_tags=data.get('topic_tags', []),
            prediction=data.get('prediction', ''),
            confidence=data.get('confidence', 0.5),
            predicted_outcome=data.get('predicted_outcome', ''),
            actual_outcome=data.get('actual_outcome'),
            correct=data.get('correct'),
            scored_at=data.get('scored_at'),
            created_at=data.get('created_at', '')
        )


@dataclass
class OverallStats:
    """Computed statistics for an agent"""
    total_simulations: int = 0
    predictions_made: int = 0
    correct: int = 0
    hit_rate: float = 0.0
    calibration_score: float = 0.0  # Brier score: 0=perfect, 1=worst
    alpha_score: float = 0.0  # hit_rate - base_rate
    avg_confidence: float = 0.0
    specializations: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OverallStats':
        return cls(
            total_simulations=data.get('total_simulations', 0),
            predictions_made=data.get('predictions_made', 0),
            correct=data.get('correct', 0),
            hit_rate=data.get('hit_rate', 0.0),
            calibration_score=data.get('calibration_score', 0.0),
            alpha_score=data.get('alpha_score', 0.0),
            avg_confidence=data.get('avg_confidence', 0.0),
            specializations=data.get('specializations', {})
        )


@dataclass
class AgentRecord:
    """Complete record for an agent"""
    agent_id: str
    persona: str
    simulations: List[SimulationRecord] = field(default_factory=list)
    overall_stats: OverallStats = field(default_factory=OverallStats)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "simulations": [s.to_dict() for s in self.simulations],
            "overall_stats": self.overall_stats.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentRecord':
        simulations = [
            SimulationRecord.from_dict(s)
            for s in data.get('simulations', [])
        ]
        stats_data = data.get('overall_stats', {})
        stats = OverallStats.from_dict(stats_data) if stats_data else OverallStats()

        return cls(
            agent_id=data.get('agent_id', ''),
            persona=data.get('persona', ''),
            simulations=simulations,
            overall_stats=stats,
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', '')
        )


class AgentLedger:
    """
    Agent Performance Ledger

    Manages a persistent JSON file tracking agent performance.
    Thread-safe operations for concurrent access.
    """

    # Default path for the ledger file
    LEDGER_DIR = os.path.join(Config.UPLOAD_FOLDER, 'data')
    LEDGER_FILE = 'agent_ledger.json'

    # Base rate for alpha calculation (random = 0.5 for binary outcomes)
    BASE_RATE = 0.5

    # Minimum simulations for full weighting
    MIN_SIMULATIONS_FOR_FULL_WEIGHT = 5

    def __init__(self, ledger_path: Optional[str] = None):
        """
        Initialize the ledger.

        Args:
            ledger_path: Optional custom path for the ledger file
        """
        if ledger_path:
            self.ledger_path = ledger_path
        else:
            os.makedirs(self.LEDGER_DIR, exist_ok=True)
            self.ledger_path = os.path.join(self.LEDGER_DIR, self.LEDGER_FILE)

        logger.info(f"AgentLedger initialized: {self.ledger_path}")

    def load(self) -> Dict[str, AgentRecord]:
        """
        Load the ledger from disk.

        Returns:
            Dictionary mapping agent_id to AgentRecord
        """
        with _ledger_lock:
            if not os.path.exists(self.ledger_path):
                logger.debug("Ledger file does not exist, returning empty ledger")
                return {}

            try:
                with open(self.ledger_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                ledger = {}
                for agent_id, agent_data in data.get('agents', {}).items():
                    ledger[agent_id] = AgentRecord.from_dict(agent_data)

                logger.debug(f"Loaded ledger with {len(ledger)} agents")
                return ledger

            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error loading ledger: {e}")
                return {}

    def save(self, ledger: Dict[str, AgentRecord]) -> None:
        """
        Save the ledger to disk.

        Args:
            ledger: Dictionary mapping agent_id to AgentRecord
        """
        with _ledger_lock:
            data = {
                "version": "1.0",
                "updated_at": datetime.now().isoformat(),
                "agents": {
                    agent_id: agent.to_dict()
                    for agent_id, agent in ledger.items()
                }
            }

            # Write to temp file first, then rename for atomicity
            temp_path = self.ledger_path + '.tmp'
            os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)

            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            os.replace(temp_path, self.ledger_path)
            logger.debug(f"Saved ledger with {len(ledger)} agents")

    def record_prediction(
        self,
        agent_id: str,
        persona: str,
        simulation_id: str,
        question: str,
        prediction: str,
        confidence: float,
        predicted_outcome: str,
        topic_tags: List[str]
    ) -> SimulationRecord:
        """
        Record a new prediction from an agent.

        Args:
            agent_id: Unique identifier for the agent
            persona: Description of the agent type
            simulation_id: ID of the simulation
            question: The question being predicted
            prediction: The agent's prediction text
            confidence: Confidence level (0-1)
            predicted_outcome: Categorical outcome (e.g., "bullish")
            topic_tags: List of topic tags for the prediction

        Returns:
            The created SimulationRecord
        """
        ledger = self.load()

        # Get or create agent record
        if agent_id not in ledger:
            ledger[agent_id] = AgentRecord(
                agent_id=agent_id,
                persona=persona
            )

        agent = ledger[agent_id]

        # Update persona if provided
        if persona:
            agent.persona = persona

        # Check if prediction already exists for this simulation
        existing = next(
            (s for s in agent.simulations if s.simulation_id == simulation_id),
            None
        )

        if existing:
            logger.warning(f"Agent {agent_id} already has prediction for {simulation_id}, updating")
            existing.prediction = prediction
            existing.confidence = confidence
            existing.predicted_outcome = predicted_outcome
            existing.topic_tags = topic_tags
            record = existing
        else:
            # Create new record
            record = SimulationRecord(
                simulation_id=simulation_id,
                question=question,
                topic_tags=topic_tags,
                prediction=prediction,
                confidence=confidence,
                predicted_outcome=predicted_outcome
            )
            agent.simulations.append(record)

        # Update overall stats
        self._recompute_agent_stats(agent)
        agent.updated_at = datetime.now().isoformat()

        self.save(ledger)

        logger.info(
            f"Recorded prediction: agent={agent_id}, simulation={simulation_id}, "
            f"outcome={predicted_outcome}, confidence={confidence:.2f}"
        )

        return record

    def record_outcome(
        self,
        simulation_id: str,
        actual_outcome: str
    ) -> Dict[str, Any]:
        """
        Record the actual outcome for a simulation, scoring all agents.

        Args:
            simulation_id: The simulation ID
            actual_outcome: The actual outcome that occurred

        Returns:
            Summary of agents scored and updated stats
        """
        ledger = self.load()
        scored_agents = []
        now = datetime.now().isoformat()

        for agent_id, agent in ledger.items():
            for sim_record in agent.simulations:
                if sim_record.simulation_id == simulation_id and sim_record.actual_outcome is None:
                    # Score this prediction
                    sim_record.actual_outcome = actual_outcome
                    sim_record.correct = sim_record.predicted_outcome == actual_outcome
                    sim_record.scored_at = now

                    # Recompute agent stats
                    self._recompute_agent_stats(agent)
                    agent.updated_at = now

                    scored_agents.append({
                        "agent_id": agent_id,
                        "predicted": sim_record.predicted_outcome,
                        "actual": actual_outcome,
                        "correct": sim_record.correct,
                        "new_hit_rate": agent.overall_stats.hit_rate,
                        "new_alpha_score": agent.overall_stats.alpha_score
                    })

        if scored_agents:
            self.save(ledger)
            logger.info(
                f"Scored {len(scored_agents)} agents for simulation {simulation_id} "
                f"(actual_outcome={actual_outcome})"
            )
        else:
            logger.warning(f"No unscored predictions found for simulation {simulation_id}")

        return {
            "simulation_id": simulation_id,
            "actual_outcome": actual_outcome,
            "agents_scored": len(scored_agents),
            "details": scored_agents
        }

    def _recompute_agent_stats(self, agent: AgentRecord) -> None:
        """
        Recompute overall stats for an agent based on their simulations.

        Args:
            agent: The agent record to update
        """
        simulations = agent.simulations

        if not simulations:
            agent.overall_stats = OverallStats()
            return

        total_simulations = len(simulations)
        scored_sims = [s for s in simulations if s.correct is not None]
        predictions_made = len(scored_sims)
        correct = sum(1 for s in scored_sims if s.correct)

        # Hit rate
        hit_rate = correct / predictions_made if predictions_made > 0 else 0.0

        # Average confidence
        avg_confidence = (
            sum(s.confidence for s in simulations) / total_simulations
            if total_simulations > 0 else 0.0
        )

        # Brier score (calibration) - only for scored predictions
        # Brier = average of (confidence - correct)^2
        # 0 = perfect calibration, 1 = worst
        if predictions_made > 0:
            brier_sum = sum(
                (s.confidence - (1.0 if s.correct else 0.0)) ** 2
                for s in scored_sims
            )
            calibration_score = brier_sum / predictions_made
        else:
            calibration_score = 0.5  # Default for no data

        # Alpha score = hit_rate - base_rate
        alpha_score = hit_rate - self.BASE_RATE

        # Specializations: hit rate per topic
        specializations: Dict[str, Dict[str, int]] = {}
        for sim in scored_sims:
            for tag in sim.topic_tags:
                if tag not in specializations:
                    specializations[tag] = {"correct": 0, "total": 0}
                specializations[tag]["total"] += 1
                if sim.correct:
                    specializations[tag]["correct"] += 1

        spec_hit_rates = {
            tag: data["correct"] / data["total"]
            for tag, data in specializations.items()
            if data["total"] > 0
        }

        agent.overall_stats = OverallStats(
            total_simulations=total_simulations,
            predictions_made=predictions_made,
            correct=correct,
            hit_rate=hit_rate,
            calibration_score=calibration_score,
            alpha_score=alpha_score,
            avg_confidence=avg_confidence,
            specializations=spec_hit_rates
        )

    def get_agent_stats(self, agent_id: str) -> Optional[AgentRecord]:
        """
        Get stats for a specific agent.

        Args:
            agent_id: The agent ID

        Returns:
            AgentRecord or None if not found
        """
        ledger = self.load()
        return ledger.get(agent_id)

    def get_top_agents(
        self,
        n: int = 10,
        topic: Optional[str] = None
    ) -> List[AgentRecord]:
        """
        Get top performing agents sorted by alpha_score.

        Args:
            n: Number of agents to return
            topic: Optional topic filter

        Returns:
            List of top AgentRecords
        """
        ledger = self.load()
        agents = list(ledger.values())

        if topic:
            # Filter by topic and sort by topic-specific hit rate
            agents = [
                a for a in agents
                if topic in a.overall_stats.specializations
            ]
            agents.sort(
                key=lambda a: a.overall_stats.specializations.get(topic, 0),
                reverse=True
            )
        else:
            # Sort by alpha score
            agents.sort(
                key=lambda a: a.overall_stats.alpha_score,
                reverse=True
            )

        return agents[:n]

    def get_weighted_agents(
        self,
        topic: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Get normalized weights for agents based on their performance.

        Agents with < MIN_SIMULATIONS_FOR_FULL_WEIGHT simulations
        get reduced weighting.

        Args:
            topic: Optional topic filter

        Returns:
            Dictionary mapping agent_id to normalized weight
        """
        ledger = self.load()

        if not ledger:
            return {}

        raw_weights = {}

        for agent_id, agent in ledger.items():
            stats = agent.overall_stats

            # Get base score
            if topic and topic in stats.specializations:
                base_score = stats.specializations[topic]
            else:
                base_score = stats.hit_rate if stats.hit_rate > 0 else 0.5

            # Apply discount for new agents
            sim_count = stats.total_simulations
            if sim_count < self.MIN_SIMULATIONS_FOR_FULL_WEIGHT:
                # Scale from 0.5x to 1x based on simulation count
                experience_factor = 0.5 + 0.5 * (sim_count / self.MIN_SIMULATIONS_FOR_FULL_WEIGHT)
            else:
                experience_factor = 1.0

            # Only include agents with some track record
            if sim_count > 0:
                raw_weights[agent_id] = base_score * experience_factor

        # Normalize weights to sum to 1
        total = sum(raw_weights.values())
        if total > 0:
            return {aid: w / total for aid, w in raw_weights.items()}
        else:
            # Equal weighting if no data
            count = len(raw_weights)
            return {aid: 1.0 / count for aid in raw_weights} if count > 0 else {}

    def compute_brier_score(self, agent_id: str) -> float:
        """
        Compute Brier score for a specific agent.

        Args:
            agent_id: The agent ID

        Returns:
            Brier score (0=perfect, 1=worst)
        """
        agent = self.get_agent_stats(agent_id)
        if not agent:
            return 0.5  # Default

        return agent.overall_stats.calibration_score

    def get_all_simulations(self) -> List[Dict[str, Any]]:
        """
        Get all simulations across all agents with their status.

        Returns:
            List of simulation summaries
        """
        ledger = self.load()
        simulations: Dict[str, Dict[str, Any]] = {}

        for agent_id, agent in ledger.items():
            for sim in agent.simulations:
                sim_id = sim.simulation_id

                if sim_id not in simulations:
                    simulations[sim_id] = {
                        "simulation_id": sim_id,
                        "question": sim.question,
                        "topic_tags": sim.topic_tags,
                        "created_at": sim.created_at,
                        "actual_outcome": sim.actual_outcome,
                        "scored": sim.actual_outcome is not None,
                        "scored_at": sim.scored_at,
                        "agent_count": 0,
                        "predictions": []
                    }

                simulations[sim_id]["agent_count"] += 1
                simulations[sim_id]["predictions"].append({
                    "agent_id": agent_id,
                    "predicted_outcome": sim.predicted_outcome,
                    "confidence": sim.confidence,
                    "correct": sim.correct
                })

        # Sort by created_at descending
        result = sorted(
            simulations.values(),
            key=lambda x: x.get('created_at', ''),
            reverse=True
        )

        return result

    def get_all_agents(self) -> List[AgentRecord]:
        """
        Get all agents.

        Returns:
            List of all AgentRecords
        """
        ledger = self.load()
        return list(ledger.values())
