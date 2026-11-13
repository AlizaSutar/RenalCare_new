"""Step 3 tests: the four role agents. Each has its own tool allow-list, system prompt and log name."""
import pytest
from fastapi.testclient import TestClient
import db
from agentic.loop import run_agent
from agentic.llm import ScriptedRoleBrain
from agentic.roles import ROLES


def used(out):
    return [t["tool"] for t in out["trace"] if t["kind"] == "tool"]


@pytest.mark.parametrize("rid", list(ROLES))
def test_each_role_finishes_with_a_verified_answer(rid):
    role = ROLES[rid]
    out = run_agent(role.goal, "DEMO-001", ScriptedRoleBrain(role), role=role)
    assert out["finished"] and out["verification"]["ok"], out["verification"]
    assert out["agent"] == role.name
    assert set(used(out)) <= set(role.tools)           # it only ever used its own tools


def test_roles_have_different_tool_lists():
    assert ROLES["diet"].tools != ROLES["management"].tools
    assert "get_care_gaps" not in ROLES["diet"].tools and "get_care_gaps" in ROLES["management"].tools


class AsksForForbiddenTool:
    """A diet agent that tries to use a tool that is not on its list."""
    name = "forbidden"
    def __init__(self): self.i = 0
    def next_step(self, goal, history, tools, system=None):
        self.i += 1
        if self.i == 1:
            return {"type": "tool_call", "name": "get_care_gaps", "args": {}}
        return {"type": "final", "text": "Nothing further to add for this patient today."}


def test_role_cannot_use_a_tool_outside_its_list():
    role = ROLES["diet"]
    out = run_agent(role.goal, "DEMO-001", AsksForForbiddenTool(), role=role)
    assert out["trace"][0]["kind"] == "blocked"
    assert used(out) == []


class RecordsWhatItIsGiven:
    name = "recorder"
    def __init__(self): self.system, self.tool_names = None, None
    def next_step(self, goal, history, tools, system=None):
        self.system, self.tool_names = system, [t["name"] for t in tools]
        return {"type": "final", "text": "Nothing further to add for this patient today."}


def test_brain_receives_the_roles_prompt_and_only_its_tools():
    role, b = ROLES["diet"], RecordsWhatItIsGiven()
    run_agent(role.goal, "DEMO-001", b, role=role)
    assert b.system == role.system and b.tool_names == role.tools


class BrokenModel:
    name = "offline"
    def next_step(self, goal, history, tools, system=None):
        raise ConnectionError("model server not running")


def test_a_crashing_brain_is_caught():
    out = run_agent("x", "DEMO-001", BrokenModel(), role=ROLES["risk"])
    assert out["finished"] is False and out["trace"][-1]["kind"] == "brain_error" and "error" in out


def test_role_runs_are_logged_under_the_role_name():
    n0 = db.one("SELECT count(*) AS n FROM agent_runs WHERE agent='Risk Assessment Agent'")["n"]
    out = run_agent(ROLES["risk"].goal, "DEMO-003", ScriptedRoleBrain(ROLES["risk"]), role=ROLES["risk"])
    n1 = db.one("SELECT count(*) AS n FROM agent_runs WHERE agent='Risk Assessment Agent'")["n"]
    assert n1 - n0 == len(out["trace"])


def test_briefing_run_does_not_overwrite_the_saved_pipeline_report():
    """Bug fixed in this step: the briefing loop used to log as 'orchestrator' and hid the pipeline report."""
    import orchestrator
    from agentic.llm import ScriptedBrain
    orchestrator.run("DEMO-001")
    run_agent("brief", "DEMO-001", ScriptedBrain())
    assert "agents" in orchestrator.latest("DEMO-001")


def test_endpoint(doc_client=None):
    from main import app
    with TestClient(app) as c:
        r = c.post("/api/login", json={"username": "dr.morgan", "password": "doctor123"})
        h = {"Authorization": "Bearer " + r.json()["token"]}
        ok = c.post("/api/patients/DEMO-001/agent/risk", headers=h)
        assert ok.status_code == 200 and ok.json()["finished"] and ok.json()["agent"] == "Risk Assessment Agent"
        assert c.post("/api/patients/DEMO-001/agent/nope", headers=h).status_code == 404
        assert c.post("/api/patients/DEMO-001/agent/risk").status_code == 401        # not logged in
        d = c.post("/api/login", json={"username": "reception1", "password": "reception123"})
        rh = {"Authorization": "Bearer " + d.json()["token"]}
        assert c.post("/api/patients/DEMO-001/agent/risk", headers=rh).status_code == 403   # reception cannot