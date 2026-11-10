"""
Simulation Outcome Resolver
Allows recording the actual outcome of a past simulation so agents can be scored.
"""

import os
import json
from typing import Dict, Any, Optional
from datetime import datetime

from ..config import Config
from ..utils.logger import get_logger
from .agent_ledger import AgentLedger

logger = get_logger('mirofish.outcome_resolver')


class OutcomeResolver:
    """
    Handles resolution of simulation outcomes.

    When an actual outcome is known, this service:
    1. Updates the agent ledger with the actual outcome
    2. Scores all agents who made predictions
    3. Updates hit rates, calibration, and alpha scores
    """

    # Path to store outcome history
    OUTCOMES_DIR = os.path.join(Config.UPLOAD_FOLDER, 'data', 'outcomes')

    def __init__(self, ledger: Optional[AgentLedger] = None):
        """
        Initialize the outcome resolver.

        Args:
            ledger: Optional AgentLedger instance (creates new one if not provided)
        """
        self.ledger = ledger or AgentLedger()
        os.makedirs(self.OUTCOMES_DIR, exist_ok=True)
        logger.info("OutcomeResolver initialized")

    def resolve_simulation(
        self,
        simulation_id: str,
        actual_outcome: str,
        notes: str = ""
    ) -> Dict[str, Any]:
        """
        Resolve a simulation by recording its actual outcome.

        This method:
        1. Records the outcome in a separate outcome history file
        2. Updates the agent ledger with the actual outcome for all agents
        3. Computes correct/incorrect for each agent
        4. Updates hit_rate, calibration, alpha_score in the ledger

        Args:
            simulation_id: The simulation ID to resolve
            actual_outcome: The actual outcome that occurred (e.g., "bullish", "bearish")
            notes: Optional notes about the resolution

        Returns:
            Summary of the resolution including agents scored and updated stats
        """
        logger.info(f"Resolving simulation {simulation_id} with outcome '{actual_outcome}'")

        # Validate inputs
        if not simulation_id:
            raise ValueError("simulation_id is required")
        if not actual_outcome:
            raise ValueError("actual_outcome is required")

        # Normalize outcome
        actual_outcome = actual_outcome.strip().lower()

        # Check if already resolved
        existing = self._get_outcome_record(simulation_id)
        if existing:
            logger.warning(
                f"Simulation {simulation_id} was already resolved with outcome "
                f"'{existing.get('actual_outcome')}'. Updating to '{actual_outcome}'."
            )

        # Score agents in the ledger
        scoring_result = self.ledger.record_outcome(
            simulation_id=simulation_id,
            actual_outcome=actual_outcome
        )

        # Record the outcome in history
        outcome_record = {
            "simulation_id": simulation_id,
            "actual_outcome": actual_outcome,
            "notes": notes,
            "resolved_at": datetime.now().isoformat(),
            "agents_scored": scoring_result.get("agents_scored", 0),
            "scoring_details": scoring_result.get("details", [])
        }

        self._save_outcome_record(simulation_id, outcome_record)

        # Build response with agent stats
        response = {
            "success": True,
            "simulation_id": simulation_id,
            "actual_outcome": actual_outcome,
            "notes": notes,
            "resolved_at": outcome_record["resolved_at"],
            "agents_scored": scoring_result.get("agents_scored", 0),
            "agent_updates": []
        }

        # Include updated stats for each scored agent
        for detail in scoring_result.get("details", []):
            agent_id = detail.get("agent_id")
            agent_record = self.ledger.get_agent_stats(agent_id)

            if agent_record:
                response["agent_updates"].append({
                    "agent_id": agent_id,
                    "persona": agent_record.persona,
                    "predicted_outcome": detail.get("predicted"),
                    "correct": detail.get("correct"),
                    "updated_stats": {
                        "total_simulations": agent_record.overall_stats.total_simulations,
                        "predictions_made": agent_record.overall_stats.predictions_made,
                        "correct": agent_record.overall_stats.correct,
                        "hit_rate": round(agent_record.overall_stats.hit_rate, 4),
                        "calibration_score": round(agent_record.overall_stats.calibration_score, 4),
                        "alpha_score": round(agent_record.overall_stats.alpha_score, 4),
                        "avg_confidence": round(agent_record.overall_stats.avg_confidence, 4)
                    }
                })

        logger.info(
            f"Simulation {simulation_id} resolved: "
            f"{response['agents_scored']} agents scored"
        )

        return response

    def get_resolution_history(
        self,
        limit: int = 50
    ) -> list:
        """
        Get history of resolved simulations.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of outcome records sorted by resolved_at descending
        """
        records = []

        if not os.path.exists(self.OUTCOMES_DIR):
            return records

        for filename in os.listdir(self.OUTCOMES_DIR):
            if filename.endswith('.json'):
                filepath = os.path.join(self.OUTCOMES_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                        records.append(record)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Error reading outcome file {filename}: {e}")

        # Sort by resolved_at descending
        records.sort(
            key=lambda x: x.get('resolved_at', ''),
            reverse=True
        )

        return records[:limit]

    def get_simulation_resolution(
        self,
        simulation_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get resolution details for a specific simulation.

        Args:
            simulation_id: The simulation ID

        Returns:
            The outcome record or None if not resolved
        """
        return self._get_outcome_record(simulation_id)

    def _get_outcome_record(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Load outcome record for a simulation."""
        filepath = os.path.join(self.OUTCOMES_DIR, f"{simulation_id}.json")

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading outcome record for {simulation_id}: {e}")
            return None

    def _save_outcome_record(
        self,
        simulation_id: str,
        record: Dict[str, Any]
    ) -> None:
        """Save outcome record to disk."""
        filepath = os.path.join(self.OUTCOMES_DIR, f"{simulation_id}.json")

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        logger.debug(f"Saved outcome record for {simulation_id}")

    def is_simulation_resolved(self, simulation_id: str) -> bool:
        """
        Check if a simulation has been resolved.

        Args:
            simulation_id: The simulation ID

        Returns:
            True if resolved, False otherwise
        """
        return self._get_outcome_record(simulation_id) is not None
