"""STEP 4: THE COORDINATOR.

Before:  orchestrator.py ran the four agents in a FIXED order, whatever the patient looked like.
Now:     a Coordinator agent (a model) reads the situation and DECIDES which specialist agents to ask.
         Its only tool is ask_agent(role, task). Each specialist is a role agent from Step 1.

Safety net (this is what makes it usable in a clinic demo):
  * If the coordinator crashes, runs out of steps, or the model is offline, we fall back to the
    fixed pipeline (orchestrator.run), so the doctor always gets a report.
  * The coordinator's answer goes through the same plain-code verifier. If it fails, the answer is still
    returned but flagged needs_attention=True, and the UI shows the badge.
  * Both ways are kept so you can COMPARE them (that comparison is a result you can report).
"""
import orchestrator
from .loop import run_agent
from .llm import get_brain
from .roles import COORDINATOR


def _agent_cards(ctx_extra):
    """One card per specialist the coordinator asked, in the shape doctor.js expects."""
    return [{"id": rid, **res, "transport": ctx_extra["called"].get(rid)} for rid, res in ctx_extra["specialist_results"].items()]


def run_coordinator(study_id, brain_for=get_brain):
    """brain_for(role) -> a brain. Defaults to get_brain (real model if configured, else scripted test brains)."""
    ctx_extra = {"brain_for": brain_for, "called": {}, "specialist_results": {}}
    try:
        out = run_agent(COORDINATOR.goal, study_id, brain_for(COORDINATOR), role=COORDINATOR, ctx_extra=ctx_extra)
    except Exception as e:                       # even a bug here must not leave the doctor with nothing
        out = {"finished": False, "error": str(e)[:200], "trace": []}
    called = list(ctx_extra["called"])
    if out.get("finished"):
        v = out["verification"]
        return {"mode": "agentic", "answer": out["answer"], "agents_called": called,
                "transport": dict(ctx_extra["called"]), "agents": _agent_cards(ctx_extra),
                "verification": v, "needs_attention": not v["ok"],
                "brain": out["brain"], "trace": out["trace"]}
    report = orchestrator.run(study_id)          # the fixed pipeline
    return {"mode": "fallback", "fallback_reason": out.get("error") or "The coordinator did not finish.",
            "agents_called": called, "transport": dict(ctx_extra["called"]),
            "agents": [], "report": report, "needs_attention": True}