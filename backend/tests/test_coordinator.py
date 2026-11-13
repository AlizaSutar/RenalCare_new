"""Step 4 tests: the coordinator agent and its fallback."""
from fastapi.testclient import TestClient
import db
from agentic.loop import run_agent
from agentic.llm import ScriptedCoordinatorBrain, ScriptedRoleBrain
from agentic.roles import ROLES, COORDINATOR
from agentic.coordinator import run_coordinator


def scripted(role):
    return ScriptedCoordinatorBrain() if role.id == "coordinator" else ScriptedRoleBrain(role)


def test_coordinator_asks_specialists_and_answer_is_verified():
    out = run_coordinator("DEMO-001", brain_for=scripted)
    assert out["mode"] == "agentic"
    assert out["agents_called"] == ["clinical_data", "risk", "management", "diet"]
    assert out["verification"]["ok"], out["verification"]          # evidence from the specialists reaches the verifier
    assert "G3b A2" in out["answer"]


def test_coordinator_is_not_a_specialist():
    assert "coordinator" not in ROLES                              # ask_agent can never start another coordinator
    assert COORDINATOR.tools == ["ask_agent"]


class SkipsDiet:
    name = "skips-diet"
    def next_step(self, goal, history, tools, system=None):
        asked = [h["args"].get("role") for h in history]
        for rid in ("clinical_data", "risk"):
            if rid not in asked:
                return {"type": "tool_call", "name": "ask_agent", "args": {"role": rid, "task": ""}}
        parts = [h["result"]["answer"] for h in history if h["result"].get("answer")]
        return {"type": "final", "text": "\n".join(parts) + "\nDiet agent skipped for this summary."}


def test_coordinator_can_choose_to_skip_agents():
    out = run_coordinator("DEMO-001", brain_for=lambda r: SkipsDiet() if r.id == "coordinator" else ScriptedRoleBrain(r))
    assert out["mode"] == "agentic" and out["agents_called"] == ["clinical_data", "risk"]


class AsksTwiceAndUnknown:
    name = "bad-asks"
    def __init__(self): self.i = 0
    def next_step(self, goal, history, tools, system=None):
        self.i += 1
        calls = [("risk", ""), ("risk", ""), ("hacker", "")]
        if self.i <= 3:
            return {"type": "tool_call", "name": "ask_agent", "args": {"role": calls[self.i - 1][0], "task": ""}}
        return {"type": "final", "text": "Nothing further to add for this patient today."}


def test_repeat_and_unknown_specialists_are_refused():
    ctx_brain = lambda r: AsksTwiceAndUnknown() if r.id == "coordinator" else ScriptedRoleBrain(r)
    out = run_agent(COORDINATOR.goal, "DEMO-001", AsksTwiceAndUnknown(), role=COORDINATOR,
                    ctx_extra={"brain_for": ctx_brain, "called": {}})
    results = [t["result"] for t in out["trace"] if t["kind"] == "tool"]
    assert "agent" in results[0] and "already asked" in results[1]["error"] and "unknown agent" in results[2]["error"]


class OtherToolBrain:
    name = "wrong-tool"
    def __init__(self): self.i = 0
    def next_step(self, goal, history, tools, system=None):
        self.i += 1
        if self.i == 1:
            return {"type": "tool_call", "name": "get_care_gaps", "args": {}}      # coordinator may only use ask_agent
        return {"type": "final", "text": "Nothing further to add for this patient today."}


def test_coordinator_cannot_read_patient_data_directly():
    out = run_agent(COORDINATOR.goal, "DEMO-001", OtherToolBrain(), role=COORDINATOR)
    assert out["trace"][0]["kind"] == "blocked"


class Offline:
    name = "offline"
    def next_step(self, goal, history, tools, system=None):
        raise ConnectionError("model server not running")


def test_model_offline_falls_back_to_the_fixed_pipeline():
    out = run_coordinator("DEMO-001", brain_for=lambda r: Offline())
    assert out["mode"] == "fallback" and "fallback_reason" in out
    assert [a["name"] for a in out["report"]["agents"]][0] == "Clinical Data Agent"


class BrokenFactory:
    def __call__(self, role): raise RuntimeError("cannot build brain")


def test_even_a_bug_in_the_coordinator_falls_back():
    assert run_coordinator("DEMO-001", brain_for=BrokenFactory())["mode"] == "fallback"


def test_coordinator_run_is_logged_and_pipeline_report_survives():
    import orchestrator
    orchestrator.run("DEMO-001")
    n0 = db.one("SELECT count(*) AS n FROM agent_runs WHERE agent='Coordinator Agent'")["n"]
    run_coordinator("DEMO-001", brain_for=scripted)
    assert db.one("SELECT count(*) AS n FROM agent_runs WHERE agent='Coordinator Agent'")["n"] > n0
    assert "agents" in orchestrator.latest("DEMO-001")


def test_endpoint():
    from main import app
    with TestClient(app) as c:
        h = {"Authorization": "Bearer " + c.post("/api/login", json={"username": "dr.morgan", "password": "doctor123"}).json()["token"]}
        r = c.post("/api/patients/DEMO-001/agentic-pipeline", headers=h)
        assert r.status_code == 200 and r.json()["mode"] == "agentic"
        assert c.post("/api/patients/NOPE/agentic-pipeline", headers=h).status_code == 404
        assert c.post("/api/patients/DEMO-001/agentic-pipeline").status_code == 401