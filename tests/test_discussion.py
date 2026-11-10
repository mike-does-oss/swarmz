"""What a panel member reads when asked for its final probability."""

from swarmz.arms.runner import _swarm_context
from swarmz.bridge import SwarmAgent


def test_final_question_context_is_brief_then_discussion():
    a = SwarmAgent(0, "p", "persona", posts=["mine"], discussion="[Post 1] Wary Lynx: hello")
    ctx = _swarm_context("THE BRIEF", a)
    assert ctx.startswith("THE BRIEF\n\nTHE PANEL'S DISCUSSION") and ctx.endswith("[Post 1] Wary Lynx: hello")
    assert "What you posted" not in ctx
    assert "(there was no discussion)" in _swarm_context("THE BRIEF", None)
    no_thread = SwarmAgent(0, "p", "persona", posts=["mine"])   # a runner that returns no thread
    assert "What you posted:\n- mine" in _swarm_context("B", no_thread)
