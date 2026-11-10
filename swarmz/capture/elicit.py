"""Per-agent final probability elicitation.

Every agent is asked directly for a probability. Output is validated with
Pydantic and retried once on invalid output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from ..llm import LLM, LLMOutputError

ELICIT_SYSTEM = (
    "You are one forecaster in a group. Stay in character as the persona described. "
    "Give your own probability that the question resolves YES, based only on the material "
    "provided. Do not guess what other forecasters or any market think."
)


class Elicited(BaseModel):
    prob: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=4000)


def build_user(persona: str, question: str, context: str, context_label: str) -> str:
    return (f"PERSONA:\n{persona}\n\nQUESTION:\n{question}\n\n{context_label}:\n{context or '(none)'}\n\n"
            'Return JSON: {"prob": <number between 0 and 1>, "rationale": "<2-4 sentences>"}')


def elicit(llm: LLM, *, model: str, persona: str, question: str, context: str,
           context_label: str = "YOUR MEMORY OF THE DISCUSSION") -> Elicited:
    user = build_user(persona, question, context, context_label)
    last: Exception | None = None
    for attempt in range(2):
        try:
            raw = llm.json(ELICIT_SYSTEM, user if attempt == 0 else
                           user + "\n\nYour previous reply was invalid. Reply with exactly that JSON object.",
                           model=model, max_tokens=600)
            return Elicited.model_validate(raw)
        except (ValidationError, LLMOutputError, TypeError) as e:
            last = e
    raise ValueError(f"elicitation failed twice: {last}")
