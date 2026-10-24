"""Session judge (plan 13.3): grades a finished session against the planner's moves."""

from __future__ import annotations

import json

from daybook.agents.base import Deps, structured
from daybook.agents.schemas import JudgeScores
from daybook.llm.gemini import user_text
from daybook.obs.tracer import Trace
from daybook.prompts import load


def flagged(scores: dict) -> bool:
    return (scores.get("goal_adherence", 1) < 0.8 or scores.get("fidelity_issues", 0) > 0
            or scores.get("solutionizing", 0) > 1)


async def judge(deps: Deps, trace: Trace, turns: list[dict]) -> JudgeScores | None:
    system, version = load("judge_session")
    convo = [{"seq": t["seq"], "role": t["role"], "text": t["text"],
              **({"move": t["move"]} if t["role"] == "assistant" else {})}
             for t in turns if not t.get("safety")]
    scores, _ = await structured(
        deps, trace, "judge.session", deps.settings.model_judge_session, system, version,
        [user_text(json.dumps(convo, ensure_ascii=False))], JudgeScores,
    )
    return scores
