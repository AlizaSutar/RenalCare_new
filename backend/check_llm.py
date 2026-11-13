"""CHECK A MODEL BEFORE YOU USE IT.   (made for the friend who runs Ollama)

Two modes:

  python check_llm.py --ping
      Needs only Python and `pip install httpx`. NO database, NO project code.
      Tests the model with a made-up patient: does it call tools, and does it write a final answer after seeing
      a tool result? Run it 3 times in one go. Prints a verdict and writes llm_check_ping.json to send back.

  python check_llm.py --full
      Needs the whole project + database (run it on the machine that has them). Runs each role agent and the
      coordinator on DEMO-001 and DEMO-007 with the real model. Prints which tools it chose and whether the
      verifier passed. Writes llm_check_full.json.

Settings (environment variables, same as the app):
  CKD_LLM_URL    default http://localhost:11434/v1     (Ollama)
  CKD_LLM_MODEL  default qwen2.5:7b-instruct
  CKD_LLM_KEY    only for hosted services
Use ONLY synthetic or demo data. Never real patient data.
"""
import json, os, sys, time

URL = os.getenv("CKD_LLM_URL", "http://localhost:11434/v1").rstrip("/")
MODEL = os.getenv("CKD_LLM_MODEL", "qwen2.5:7b-instruct")
KEY = os.getenv("CKD_LLM_KEY", "none")

DUMMY_TOOLS = [
    {"type": "function", "function": {"name": "get_patient_summary", "description": "Get the patient's age, eGFR and KDIGO stage. ALWAYS call this first.",
                                      "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "search_guidelines", "description": "Search guideline text. Returns passages with document and page.",
                                      "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]
SYSTEM = ("You are a careful clinical-support assistant. Use your tools. Quote numbers exactly as the tools return them. "
          "Cite the guideline page in square brackets. You only suggest; never say you changed anything. Under 80 words.")


def post(path, body):
    import httpx
    r = httpx.post(URL + path, json=body, timeout=300, headers={"Authorization": f"Bearer {KEY}"})
    r.raise_for_status()
    return r.json()


def ping():
    import httpx
    print(f"URL={URL}\nMODEL={MODEL}\n")
    try:
        m = httpx.get(URL + "/models", timeout=15, headers={"Authorization": f"Bearer {KEY}"}).json()
        names = [x["id"] for x in m.get("data", [])]
        print("Models the server offers:", ", ".join(names) or "(none)")
        if MODEL not in names:
            print(f"WARNING: '{MODEL}' is not in that list. Run:  ollama pull {MODEL}")
    except Exception as e:
        print("Cannot reach the server:", str(e)[:150]); return False
    results = []
    for trial in range(1, 4):
        t0 = time.time()
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Prepare a short briefing for patient DEMO-001. Use your tools first."}]
        row = {"trial": trial, "called_tool": False, "tool": None, "final_after_result": False, "answer": None, "cited": False, "error": None}
        try:
            out = post("/chat/completions", {"model": MODEL, "messages": msgs, "tools": DUMMY_TOOLS, "temperature": 0.1})
            m1 = out["choices"][0]["message"]
            if m1.get("tool_calls"):
                c = m1["tool_calls"][0]
                row["called_tool"], row["tool"] = True, c["function"]["name"]
                msgs.append({"role": "assistant", "content": None, "tool_calls": [c]})
                msgs.append({"role": "tool", "tool_call_id": c.get("id", "call_0"),
                             "content": json.dumps({"age": 63, "egfr": 41, "kdigo_stage": "G3b A2",
                                                    "passages": [{"doc": "KDIGO 2024 CKD", "page": "S197"}]})})
                out2 = post("/chat/completions", {"model": MODEL, "messages": msgs, "tools": DUMMY_TOOLS, "temperature": 0.1})
                m2 = out2["choices"][0]["message"]
                txt = (m2.get("content") or "").strip()
                row["final_after_result"] = bool(txt) and not m2.get("tool_calls")
                row["answer"] = txt[:300]
                row["cited"] = "[KDIGO 2024 CKD S197]" in txt or "KDIGO 2024 CKD S197" in txt
        except Exception as e:
            row["error"] = str(e)[:150]
        row["seconds"] = round(time.time() - t0, 1)
        results.append(row)
        print(f"trial {trial}: tool={row['tool']}  final={row['final_after_result']}  cited={row['cited']}  {row['seconds']}s  {row['error'] or ''}")
    ok = sum(r["called_tool"] and r["final_after_result"] for r in results)
    print(f"\nRESULT: {ok}/3 trials used a tool AND wrote a final answer.")
    print("Good enough to try the agents" if ok >= 2 else "Weak: try another model (a tool-capable one such as qwen2.5, llama3.1 or llama3.2).")
    json.dump({"url": URL, "model": MODEL, "trials": results}, open("llm_check_ping.json", "w"), indent=2)
    print("Saved llm_check_ping.json - send this file back.")
    return ok >= 2


def full():
    from agentic.llm import OpenAICompatBrain
    from agentic.loop import run_agent
    from agentic.roles import ROLES
    from agentic.coordinator import run_coordinator
    brain_for = lambda role=None: OpenAICompatBrain()
    rows = []
    for sid in ("DEMO-001", "DEMO-007"):
        for rid, role in ROLES.items():
            out = run_agent(role.goal, sid, OpenAICompatBrain(), role=role)
            used = [t["tool"] for t in out["trace"] if t["kind"] == "tool"]
            bad = [t for t in out["trace"] if t["kind"] == "blocked"]
            rows.append({"patient": sid, "agent": rid, "finished": out["finished"], "tools": used, "blocked_calls": len(bad),
                         "verified": (out.get("verification") or {}).get("ok"), "problems": (out.get("verification") or {}).get("problems")})
            print(f"{sid} {rid:14} finished={out['finished']!s:5} tools={used} blocked={len(bad)} verified={rows[-1]['verified']}")
        c = run_coordinator(sid, brain_for=brain_for)
        rows.append({"patient": sid, "agent": "coordinator", "mode": c["mode"], "agents_called": c["agents_called"],
                     "verified": (c.get("verification") or {}).get("ok"), "fallback_reason": c.get("fallback_reason")})
        print(f"{sid} coordinator    mode={c['mode']} asked={c['agents_called']} verified={rows[-1]['verified']} {c.get('fallback_reason') or ''}")
    json.dump({"url": URL, "model": MODEL, "rows": rows}, open("llm_check_full.json", "w"), indent=2)
    print("Saved llm_check_full.json")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--ping"
    full() if mode == "--full" else ping()