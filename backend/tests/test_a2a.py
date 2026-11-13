"""Step 5 tests: the specialists as real A2A servers, called over real HTTP on this computer."""
import socket
import threading
import time

import httpx
import pytest
from starlette.testclient import TestClient

import db
from agentic.llm import ScriptedCoordinatorBrain, ScriptedRoleBrain
from agentic.roles import ROLES
from agentic.tools import run_specialist
from agentic.coordinator import run_coordinator
from a2a_agents import config, server
from a2a_agents.client import ask_via_a2a


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def scripted(role):
    return ScriptedCoordinatorBrain() if role.id == "coordinator" else ScriptedRoleBrain(role)


@pytest.fixture(scope="module")
def servers():
    mp = pytest.MonkeyPatch()
    for var in ("CKD_LLM_URL", "ANTHROPIC_API_KEY", "CKD_A2A_TOKEN"):        # servers must use the scripted test brains
        mp.delenv(var, raising=False)
    started = []
    for rid in ROLES:
        port = free_port()
        mp.setenv(f"CKD_A2A_{rid.upper()}_URL", f"http://127.0.0.1:{port}")
        srv = server.make_server(rid, port=port)
        threading.Thread(target=srv.run, daemon=True).start()
        started.append(srv)
    for srv in started:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.1)
        assert srv.started
    yield
    for srv in started:
        srv.should_exit = True
    mp.undo()


def test_every_specialist_publishes_an_agent_card(servers):
    for rid, role in ROLES.items():
        card = httpx.get(config.url(rid) + "/.well-known/agent-card.json").json()
        assert card["name"] == role.name and card["skills"][0]["id"] == rid
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"


def test_a2a_answer_matches_the_in_process_answer(servers):
    for rid in ROLES:
        remote = ask_via_a2a(rid, "DEMO-001")
        local = run_specialist(rid, "DEMO-001")
        assert remote["answer"] == local["answer"] and remote["verified"] is True
        assert remote["tools_used"] == local["tools_used"] and remote["passages"] == local["passages"]


def test_coordinator_reaches_all_specialists_over_a2a(servers, monkeypatch):
    monkeypatch.setenv("CKD_A2A", "1")
    out = run_coordinator("DEMO-001", brain_for=scripted)
    assert out["mode"] == "agentic" and out["verification"]["ok"], out.get("verification")
    assert out["transport"] == {rid: "a2a" for rid in ROLES}


def test_unreachable_server_falls_back_to_in_process(servers, monkeypatch):
    monkeypatch.setenv("CKD_A2A", "1")
    monkeypatch.setenv("CKD_A2A_RISK_URL", f"http://127.0.0.1:{free_port()}")      # nothing listens here
    out = run_coordinator("DEMO-001", brain_for=scripted)
    assert out["mode"] == "agentic" and out["verification"]["ok"]
    assert out["transport"]["risk"].startswith("in-process (A2A unreachable")
    assert out["transport"]["diet"] == "a2a"


def test_a_failed_specialist_is_reported_not_hidden(servers):
    r = ask_via_a2a("risk", "NOPE-999")
    assert "error" in r and "Unknown patient" in r["error"]


def test_study_id_text_never_reaches_the_database(servers):
    r = ask_via_a2a("risk", "'; DROP TABLE patients;--")
    assert "error" in r
    assert db.one("SELECT count(*) AS n FROM patients")["n"] > 0


def test_token_gate(monkeypatch):
    monkeypatch.setenv("CKD_A2A_TOKEN", "secret")
    c = TestClient(server.create_app("risk", base_url="http://127.0.0.1:1"))
    assert c.get("/.well-known/agent-card.json").status_code == 200          # the card stays public
    assert c.post("/", json={}).status_code == 401
    assert c.post("/", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/", json={}, headers={"Authorization": "Bearer secret"}).status_code != 401


def test_unknown_agent_name_is_refused_by_the_launcher():
    with pytest.raises(KeyError):
        server.create_app("hacker")