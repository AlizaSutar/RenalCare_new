"""Watch the agent work.   From the backend folder:
      python -m agentic.run_demo DEMO-001              (the general briefing agent)
      python -m agentic.run_demo DEMO-001 diet         (one role: clinical_data, risk, management or diet)
      python -m agentic.run_demo DEMO-001 coordinator  (the coordinator asks the specialists)
It uses the real model if you set ANTHROPIC_API_KEY or CKD_LLM_URL, otherwise a scripted test brain."""
import sys, json
from .loop import run_agent
from .llm import get_brain
from .roles import ROLES


def short(x, n=170):
    s = json.dumps(x, default=str)
    return s if len(s) <= n else s[:n] + " ..."


def coordinator_demo(study_id):
    from .coordinator import run_coordinator
    out = run_coordinator(study_id)
    print(f"\nCOORDINATOR   mode={out['mode']}   brain={out['brain']}   asked={out['agents_called']}")
    print(f"transport per specialist: {out.get('transport')}")
    print(f"verification ok = {(out.get('verification') or {}).get('ok')}\n")
    print(out.get("answer") or f"FALLBACK to the fixed pipeline. Reason: {out.get('fallback_reason')}")


def main(study_id, role_id=None):
    if role_id == "coordinator":
        return coordinator_demo(study_id)
    role = ROLES[role_id] if role_id else None
    goal = role.goal if role else "Prepare a pre-visit brief for the doctor"
    out = run_agent(goal, study_id, get_brain(role), role=role)
    print(f"\nAGENT: {role.name if role else 'general briefing agent'}   BRAIN: {out.get('brain')}")
    print(f"GOAL: {goal}  ({study_id})\n")
    for i, t in enumerate(out["trace"], 1):
        if t["kind"] == "tool":
            print(f"STEP {i}  ACT      call tool: {t['tool']}  {t['args'] or ''}")
            print(f"         OBSERVE  {short(t['result'])}  ({t['ms']} ms)\n")
        elif t["kind"] == "blocked":
            print(f"STEP {i}  BLOCKED  {t['tool']}: {t['reason']}\n")
        elif t["kind"] == "final":
            print(f"STEP {i}  FINAL ANSWER\n")
            print(t["text"])
        else:
            print(f"STEP {i}  STOPPED  {t['reason']}")
    print(f"\nfinished={out['finished']}  steps={out['steps']}  verification={out.get('verification', {}).get('ok')}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "DEMO-001", sys.argv[2] if len(sys.argv) > 2 else None)
