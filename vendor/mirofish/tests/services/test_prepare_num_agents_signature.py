import inspect

from app.services.simulation_manager import SimulationManager


def test_prepare_simulation_accepts_num_agents_defaulting_none():
    sig = inspect.signature(SimulationManager.prepare_simulation)
    assert "num_agents" in sig.parameters
    assert sig.parameters["num_agents"].default is None


def test_manager_module_imports_the_planner():
    import app.services.simulation_manager as mgr
    assert hasattr(mgr, "plan_agent_entities")
