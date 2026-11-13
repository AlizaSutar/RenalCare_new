"""STEP 3: EVALUATION. Does the agent version do as well as the fixed pipeline, and how often does it go wrong?

Run from the backend folder (database running, demo patients loaded with seed.py):

    python eval_agents.py                                   all demo patients, real model if CKD_LLM_URL is set
    python eval_agents.py --brain scripted                  test brains: checks THIS SCRIPT, says nothing about a model
    python eval_agents.py --patients DEMO-001,DEMO-007 --skip-coordinator      quick check (a few minutes on a 3B model)
    python eval_agents.py --repeat 3                        repeat every case 3 times (real models vary run to run)

What it measures (every check is plain code, no model judges another model):
  FIXED PIPELINE   completes? how long? how many guideline citations?
  EACH SPECIALIST  finished | used its required tools | stayed on its own tool list | passed the verifier |
                   invented a number | left out a citation | needed a retry | seconds
                   (risk agent also: stated the right KDIGO stage, and did not invent a stage when there is none)
  COORDINATOR      agentic or fell back | asked the right specialists | passed the verifier | seconds

"Right specialists" comes from the coordinator's own written rule (clinical_data and risk always; management and diet only
when there is a KDIGO stage or a watch/high screening result). It checks rule-following, NOT clinical truth.
Limits: synthetic demo patients only. This is verification of behaviour, not clinical validation.
Writes eval_results.json (every run) and eval_summary.md (a table to paste into a report). Runs are also saved in agent_runs.
"""
import argparse, json, re, time
from datetime import datetime

import db, service, orchestrator
from agentic.loop import run_agent
from agentic.llm import get_brain, ScriptedRoleBrain, ScriptedCoordinatorBrain
from agentic.roles import ROLES
from agentic.coordinator import run_coordinator

REQUIRED = {"clinical_data": {"check_data_completeness"}, "risk": {"assess_risk"},
            "management": {"assess_risk", "get_care_gaps", "search_guidelines"}, "diet": {"search_guidelines"}}
STAGE_RE = re.compile(r"\bG[1-5][ab]?\b")


def scripted_brain_for(role):
    return ScriptedCoordinatorBrain() if role.id == "coordinator" else ScriptedRoleBrain(role)


def truth(sid):
    """What plain code says about this patient. The agents are checked against this."""
    p = service.build(sid, persist=False, include_model=True)
    k = p["kdigo"]
    res = (p["screening"] or {}).get("result")
    stage = k["stage_text"] if k["g"] else None
    level = res["level"] if res else None
    return {"stage": stage, "needs_more": bool(stage) or level in ("watch", "high")}


def stage_ok(answer, stage):
    if not answer:
        return False
    return (stage in answer) if stage else not STAGE_RE.search(answer)    # no stage on record: any stage in the text is invented


def problem(v, text):
    return any(text in p for p in (v or {}).get("problems", []))


def eval_role(sid, rid, brain_for, t):
    role = ROLES[rid]
    t0 = time.time()
    out = run_agent(role.goal, sid, brain_for(role), role=role)
    used = [x["tool"] for x in out["trace"] if x["kind"] == "tool"]
    v = out.get("verification") or {}
    return {"patient": sid, "kind": "role", "agent": rid, "brain": out.get("brain"), "finished": out["finished"],
            "required_tools_ok": REQUIRED[rid] <= set(used), "tools": used,
            "blocked_calls": sum(x["kind"] == "blocked" for x in out["trace"]),
            "retries": sum(x["kind"] == "retry" for x in out["trace"]), "steps": out["steps"],
            "verified": bool(v.get("ok")), "invented_number": problem(v, "numbers not found"),
            "missing_citation": problem(v, "no citation"), "bad_citation": problem(v, "citations not backed"),
            "claims_action": problem(v, "claims an action"),
            "stage_ok": stage_ok(out.get("answer"), t["stage"]) if rid == "risk" else None,
            "seconds": round(time.time() - t0, 1), "problems": v.get("problems", []), "error": out.get("error")}


def eval_coordinator(sid, brain_for, t):
    t0 = time.time()
    c = run_coordinator(sid, brain_for=brain_for)
    called = c["agents_called"]
    expected = {"clinical_data", "risk"} | ({"management", "diet"} if t["needs_more"] else set())
    v = c.get("verification") or {}
    agentic = c["mode"] == "agentic"
    return {"patient": sid, "kind": "coordinator", "agent": "coordinator", "brain": c.get("brain"), "mode": c["mode"],
            "asked": called, "missed": sorted(expected - set(called)), "extra": sorted(set(called) - expected),
            "right_specialists": agentic and set(called) == expected, "verified": agentic and bool(v.get("ok")),
            "stage_ok": stage_ok(c.get("answer"), t["stage"]) if agentic else None,
            "fallback_reason": c.get("fallback_reason"), "seconds": round(time.time() - t0, 1), "problems": v.get("problems", [])}


def eval_fixed(sid):
    t0 = time.time()
    try:
        r = orchestrator.run(sid)
        ok = all(a["status"] != "failed" for a in r["agents"])
        return {"patient": sid, "kind": "fixed", "agent": "fixed pipeline", "completed": ok,
                "citations": len(r["citations"]), "seconds": round(time.time() - t0, 1)}
    except Exception as e:
        return {"patient": sid, "kind": "fixed", "agent": "fixed pipeline", "completed": False, "citations": 0,
                "seconds": round(time.time() - t0, 1), "error": str(e)[:120]}


def pct(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return f"{100 * sum(bool(x) for x in vals) / len(vals):.0f}%" if vals else "n/a"


def avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return f"{sum(vals) / len(vals):.1f}" if vals else "n/a"


def summarise(rows):
    S = {}
    fx = [r for r in rows if r["kind"] == "fixed"]
    if fx:
        S["fixed pipeline"] = {"runs": len(fx), "completed": pct(fx, "completed"), "avg citations": avg(fx, "citations"), "avg seconds": avg(fx, "seconds")}
    for rid in ROLES:
        rs = [r for r in rows if r["kind"] == "role" and r["agent"] == rid]
        if rs:
            S[rid] = {"runs": len(rs), "finished": pct(rs, "finished"), "required tools": pct(rs, "required_tools_ok"),
                      "verified": pct(rs, "verified"), "invented number": pct(rs, "invented_number"),
                      "missing citation": pct(rs, "missing_citation"), "retried": pct([{"x": r["retries"] > 0} for r in rs], "x"),
                      "stage correct": pct(rs, "stage_ok"), "avg seconds": avg(rs, "seconds")}
    co = [r for r in rows if r["kind"] == "coordinator"]
    if co:
        S["coordinator"] = {"runs": len(co), "agentic (not fallback)": pct([{"x": r["mode"] == "agentic"} for r in co], "x"),
                            "right specialists": pct(co, "right_specialists"), "verified": pct(co, "verified"),
                            "stage correct": pct(co, "stage_ok"), "avg seconds": avg(co, "seconds")}
    return S


def run_eval(patients, brain_mode="env", repeat=1, skip_coordinator=False, progress=print):
    brain_for = scripted_brain_for if brain_mode == "scripted" else get_brain
    rows = []
    for rep in range(repeat):
        for sid in patients:
            t = truth(sid)
            progress(f"{sid} (round {rep + 1}): fixed pipeline")
            rows.append(eval_fixed(sid))
            for rid in ROLES:
                progress(f"{sid} (round {rep + 1}): {rid} agent")
                rows.append(eval_role(sid, rid, brain_for, t))
            if not skip_coordinator:
                progress(f"{sid} (round {rep + 1}): coordinator")
                rows.append(eval_coordinator(sid, brain_for, t))
    for i, r in enumerate(rows):
        r["case"] = i + 1
    return rows, summarise(rows)


def markdown(summary, meta):
    L = [f"# Agent evaluation ({meta['date']})", "",
         f"Brain: {', '.join(meta['brains'])}. Patients: {meta['n_patients']} synthetic demo patients, {meta['repeat']} round(s). "
         f"Runs: {meta['n_runs']}.", "",
         "Verification of behaviour on synthetic data, not clinical validation. The expected specialists come from the coordinator's own written rule.", ""]
    for name, d in summary.items():
        L += [f"## {name}", "", "| measure | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in d.items()] + [""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Evaluate the fixed pipeline, the specialist agents and the coordinator.")
    ap.add_argument("--patients", help="comma-separated study IDs (default: every patient in the database)")
    ap.add_argument("--brain", choices=["env", "scripted"], default="env", help="env = real model if configured; scripted = test brains")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--skip-coordinator", action="store_true")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    patients = [x.strip().upper() for x in a.patients.split(",")] if a.patients else [r["study_id"] for r in db.all("SELECT study_id FROM patients ORDER BY study_id")]
    rows, summary = run_eval(patients, a.brain, a.repeat, a.skip_coordinator)
    brains = sorted({r["brain"] for r in rows if r.get("brain")})
    meta = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "brains": brains, "n_patients": len(patients), "repeat": a.repeat, "n_runs": len(rows)}
    with open(f"{a.out_dir}/eval_results.json", "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, indent=2, ensure_ascii=False)
    with open(f"{a.out_dir}/eval_summary.md", "w", encoding="utf-8") as f:
        f.write(markdown(summary, meta))
    print("\n" + markdown(summary, meta))
    if any(b.startswith("scripted") for b in brains):
        print("NOTE: scripted test brains were used. This checks the wiring and this script, NOT how a real model behaves.")
    print("Saved eval_results.json and eval_summary.md")


if __name__ == "__main__":
    main()