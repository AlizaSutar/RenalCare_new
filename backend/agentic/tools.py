"""STEP 1: TOOLS.

A tool is just a normal Python function plus a short description. The description is what
the brain (the LLM) reads to decide when to use it.

Safety rules built in here:
  * Tools are READ-ONLY. None of them changes the patient record.
  * The patient is fixed by the system (ctx["study_id"]). The brain cannot ask for a
    different patient, even if it tries.
  * Names are never returned. The brain only sees the study ID.
"""
from dataclasses import dataclass
import json
from typing import Callable
import service, screening
from rag.retrieve import retrieve


@dataclass
class Tool:
    name: str
    description: str          # written for the brain: WHEN to use it
    parameters: dict          # what the brain must send (JSON schema); {} = nothing
    fn: Callable              # fn(ctx, **args) -> dict

    def schema(self):
        """The format real LLM APIs expect when you tell them which tools exist."""
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.parameters, "required": list(self.parameters)}}


def _patient(ctx, model=False):
    p = service.build(ctx["study_id"], persist=False, include_model=model)
    if not p:
        raise ValueError("patient not found")
    return p


def get_patient_summary(ctx):
    p = _patient(ctx)
    k = p["kdigo"]
    return {
        "study_id": p["id"], "age": p["age"], "sex": p["sex"],
        "conditions": [n for n, v in p["conditions"].items() if v],
        "egfr": k["egfr"], "egfr_date": k["egfr_date"], "uacr": k["uacr"], "uacr_date": k["uacr_date"],
        "kdigo_stage": k["stage_text"], "kdigo_risk": k["risk"], "egfr_slope_per_year": k["egfr_slope"],
        "has_kidney_labs": k["egfr"] is not None or k["uacr"] is not None,
        "medicines": [m["drug"] for m in p["medicines"]],
        "visits_on_record": len(p["visits"]),
    }


def get_care_gaps(ctx):
    p = _patient(ctx)
    return {"flags": [{"code": f["code"], "level": f["level"], "title": f["title"],
                       "detail": f["detail"], "source": f["source"], "doctor_status": f["status"]} for f in p["flags"]]}


def run_screening_model(ctx):
    """Random forest. Only meaningful when the patient has NO eGFR/uACR."""
    p = _patient(ctx)
    inputs = p["screening"]["inputs"]
    if inputs.get("age") is None or not inputs.get("sex"):
        return {"error": "Not enough inputs (need at least age and sex)."}
    r = screening.predict(inputs)
    return {"applicable": p["screening"]["applicable"], "level": r["level"], "message": r["message"],
            "top_reasons": [f"{e['feature']} {e['direction']} the score" for e in r["explanation"][:3]],
            "inputs_missing": r["missing_inputs"], "disclaimer": r["disclaimer"]}


def search_guidelines(ctx, question):
    """RAG tool: finds the guideline passages closest to the question. Each passage carries its
    document name and page label (e.g. 'KDIGO 2024 CKD S197') so the answer can cite it."""
    hits = retrieve(question, k=3)
    return {"passages": [{"doc": h["doc"], "page": h["page"], "text": h["text"][:500]} for h in hits]}


def check_data_completeness(ctx):
    """Plain-code check of which key values are on record, and which are missing or older than a year.
    This is the same function the fixed pipeline uses (orchestrator.clinical_data)."""
    import orchestrator
    return orchestrator.clinical_data(_patient(ctx))


def assess_risk(ctx):
    """Plain-code KDIGO stage and risk (or the screening result when there are no kidney labs).
    This is the same function the fixed pipeline uses (orchestrator.risk_assessment)."""
    import orchestrator
    return orchestrator.risk_assessment(_patient(ctx, model=True))




def run_specialist(role_id, study_id, task="", brain_for=None, upstream_context=None):
    """Run one specialist and preserve its complete tool evidence."""

    from .roles import ROLES
    from .loop import run_agent
    from .llm import get_brain

    r = ROLES.get(role_id)
    if not r:
        return {"error": f"unknown agent '{role_id}'. Choose one of: {', '.join(ROLES)}"}

    brain = brain_for(r) if brain_for else get_brain(r)

    upstream_context = upstream_context or {}
    context_note = ""

    if upstream_context:
        context_note = (
            "\n\nVerified upstream specialist findings (supplemental context):\n"
            + json.dumps(upstream_context, ensure_ascii=False, default=str)[:5000]
            + "\nUse these findings as context, but independently call your permitted tools. "
              "If tool results conflict with upstream findings, report the discrepancy and follow your tools."
        )

    goal = (
        r.goal
        + (f"\nCoordinator task: {str(task)[:500]}" if task else "")
        + context_note
    )

    out = run_agent(goal, study_id, brain, role=r)

    if not out.get("finished"):
        return {
            "agent": r.name,
            "error": f"{r.name} did not finish ({out.get('error') or 'step limit reached'}).",
            "status": "failed",
            "tools_used": [],
            "tool_results": [],
            "verified": False
        }

    tool_calls = [
        t for t in out["trace"] if t["kind"] == "tool"
    ]

    tool_results = [
        {
            "tool": t["tool"],
            "result": t["result"]
        }
        for t in tool_calls
    ]

    return {
        "agent": r.name,
        "status": "completed",
        "answer": out["answer"],
        "verified": out["verification"]["ok"],
        "verification_details": out["verification"],
        "tools_used": [
            t["tool"] for t in tool_calls
        ],
        "tool_results": tool_results,
        "passages": [
            {
                "doc": q["doc"],
                "page": q["page"]
            }
            for t in tool_calls
            for q in t["result"].get("passages", [])
        ],
        "flags": [
            {
                "source": f["source"]
            }
            for t in tool_calls
            for f in t["result"].get("flags", [])
        ]
    }




def ask_agent(ctx, role, task=""):
    """COORDINATOR-ONLY tool: asks one specialist and passes earlier findings."""
    import os
    from .roles import ROLES

    if role not in ROLES:
        return {"error": f"unknown agent '{role}'. Choose one of: {', '.join(ROLES)}"}

    called = ctx.setdefault("called", {})
    if role in called:
        return {"error": f"'{role}' was already asked. Its answer is earlier in this conversation."}

    # Findings from specialists that have already completed (the A2A hand-off).
    specialist_results = ctx.setdefault("specialist_results", {})
    keys = ("agent", "status", "answer", "error", "verified", "tools_used", "passages", "flags")
    upstream_context = {rid: {k: res[k] for k in keys if k in res} for rid, res in specialist_results.items()}
    received = list(upstream_context)                      # who this agent hears from (shown in the dashboard)

    called[role] = "pending"
    via = "in-process"

    if os.getenv("CKD_A2A") == "1":
        from a2a_agents.client import ask_via_a2a, A2AUnavailable
        try:
            res = ask_via_a2a(role, ctx["study_id"], task, upstream_context=upstream_context)
            called[role] = res["via"] = "a2a"
            res["received_from"] = received
            specialist_results[role] = res
            return res
        except A2AUnavailable as e:
            via = f"in-process (A2A unreachable: {str(e)[:60]})"

    res = run_specialist(role, ctx["study_id"], task, ctx.get("brain_for"), upstream_context=upstream_context)
    called[role] = res["via"] = via
    res["received_from"] = received
    specialist_results[role] = res
    return res


TOOLS = {t.name: t for t in [
    Tool("get_patient_summary",
         "Get the patient's age, conditions, latest eGFR and uACR, KDIGO stage and risk, eGFR trend and medicines. ALWAYS call this first.",
         {}, get_patient_summary),
    Tool("get_care_gaps",
         "List the rule-based care gaps (overdue tests, missing or unsafe medicines, fast eGFR decline, referral, BP).",
         {}, get_care_gaps),
    Tool("run_screening_model",
         "Run the machine-learning screening model. Use ONLY when the patient has no eGFR or uACR. It says whether testing is worth ordering.",
         {}, run_screening_model),
    Tool("search_guidelines",
         "Search the stored guideline text for passages that answer a question. Returns passages with document and page.",
         {"question": {"type": "string", "description": "what to look up"}}, search_guidelines),
    Tool("check_data_completeness",
         "List which key values (eGFR, uACR, creatinine, BP, potassium, HbA1c) are on record, which are missing and which are older than a year.",
         {}, check_data_completeness),
    Tool("assess_risk",
         "Get the KDIGO stage, risk tier, eGFR trend and monitoring interval. If the patient has no kidney labs it returns the screening-model result instead.",
         {}, assess_risk),
    Tool("ask_agent",
         "Ask ONE specialist agent to do its job and return its answer. role must be one of: clinical_data, risk, management, diet. "
         "task is an optional short note. Each specialist can be asked only once.",
         {"role": {"type": "string", "enum": ["clinical_data", "risk", "management", "diet"]},
          "task": {"type": "string", "description": "optional short note for that agent"}}, ask_agent),
]}