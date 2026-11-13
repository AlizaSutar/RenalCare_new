"""STEP 2 (part 2): THE LOOP.

    ask the brain  ->  it picks a tool  ->  we run the tool  ->  give the result back  ->  repeat
                  ...until the brain says 'final', or we run out of steps.

Guard rails (these are the 'safe agent' part, keep them):
  * max_steps        the agent cannot loop forever
  * allow-list       the brain may only use tools that exist in TOOLS
  * fixed patient    ctx comes from us, not from the brain
  * every step is saved in the agent_runs table, so you can show what happened
  * role (optional)  gives the agent its own goal, system prompt and its OWN tool allow-list (see roles.py)
  * a brain that crashes (no internet, model not running) is caught, not allowed to break the page
"""
import json, time
import db
from .tools import TOOLS
from .verify import verify, allowed_citations


def run_agent(goal, study_id, brain, max_steps=6, save=True, role=None, ctx_extra=None):
    ctx = {"study_id": study_id, **(ctx_extra or {})}     # ctx is private: the brain never sees it
    allowed = {n: TOOLS[n] for n in role.tools} if role else TOOLS      # a role only sees ITS tools
    agent_name = role.name if role else "agent_loop"                    # NOT "orchestrator": that name is the fixed pipeline's report
    extra = {"system": role.system} if role else {}                    # only roles pass a system prompt
    patient_row = db.one("SELECT id FROM patients WHERE study_id=%s", (study_id,))
    pid = patient_row["id"] if patient_row else None
    history, trace = [], []
    max_steps = role.max_steps if role else max_steps
    tool_list = [t.schema() for t in allowed.values()]

    def log(kind, data):
        trace.append({"kind": kind, **data})
        if save:
            db.run("INSERT INTO agent_runs(patient_id, agent, input, output, status) VALUES (%s,%s,%s,%s,%s)",
                   (pid, agent_name, json.dumps({"goal": goal, "brain": brain.name, "step": len(history)}, ensure_ascii=False),
                    json.dumps(data, default=str, ensure_ascii=False)[:15000], data.get("status", "ok")))

    retried = repaired = False                                # one retry and one citation repair per run

    for step in range(1, max_steps + 1):
        try:
            decision = brain.next_step(goal, history, tool_list, **extra)
        except Exception as e:                                   # model offline, bad reply, network error...
            log("brain_error", {"error": str(e)[:200], "status": "error"})
            return {"answer": None, "trace": trace, "steps": step, "finished": False, "brain": brain.name,
                    "error": "The model could not be reached or gave an unusable reply."}

        if decision["type"] == "final":
            text = (decision.get("text") or "").strip()
            if (not text or text.startswith("(the model returned no text)")) and not retried:
                retried = True                                   # empty reply: ask once more
                log("retry", {"reason": "empty reply", "status": "retry"})
                history.append({"tool": "system_note", "args": {}, "result": {
                    "note": "Your last reply was empty. Reply again with the final answer."}})
                continue
            check = verify(decision["text"], history)            # independent plain-code check
            cite_problem = any(p.startswith("no citation") or p.startswith("citations not backed") for p in check["problems"])
            if cite_problem and not repaired and step < max_steps:
                repaired = True                                  # wrong citation format: show the exact strings, ask once
                options = allowed_citations(history)[:6]
                log("retry", {"reason": "citation format", "status": "retry"})
                history.append({"tool": "system_note", "args": {}, "result": {
                    "note": "Rewrite your answer. Put each source in square brackets, copied exactly from this list: "
                            + "; ".join(f"[{o}]" for o in options)}})
                continue
            log("final", {"text": decision["text"], "verification": check})
            return {"answer": decision["text"], "trace": trace, "steps": step, "finished": True,
                    "verification": check, "brain": brain.name, "agent": agent_name}

        name, args = decision.get("name"), decision.get("args", {})
        if name not in allowed:                                  # allow-list check (per role)
            log("blocked", {"tool": name, "reason": "unknown tool", "status": "blocked"})
            history.append({"tool": name, "args": args, "result": {"error": "unknown tool"}})
            continue
        t0 = time.time()
        try:
            result = allowed[name].fn(ctx, **args)
        except Exception as e:                                   # a tool failure must not crash the agent
            result = {"error": str(e)[:200]}
        history.append({"tool": name, "args": args, "result": result})
        log("tool", {"tool": name, "args": args, "result": result, "ms": round((time.time() - t0) * 1000),
                     "status": "error" if "error" in result else "ok"})

    log("stopped", {"reason": f"reached max_steps={max_steps}", "status": "stopped"})
    return {"answer": None, "trace": trace, "steps": max_steps, "finished": False, "brain": brain.name}