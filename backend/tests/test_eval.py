"""Tests for eval_agents.py: does the evaluation script itself notice good and bad behaviour?"""
import json
import eval_agents as ev
from agentic.llm import ScriptedRoleBrain


def test_stage_check():
    assert ev.stage_ok("Stage is G3b A2, very high risk.", "G3b A2")
    assert not ev.stage_ok("Stage is G2 A1.", "G3b A2")
    assert ev.stage_ok("Not enough kidney results to stage.", None)
    assert not ev.stage_ok("Looks like G3a to me.", None)        # no stage on record, so a stage in the text is invented


def test_scripted_run_scores_clean_and_summarises():
    rows, summary = ev.run_eval(["DEMO-001", "DEMO-007"], brain_mode="scripted")
    assert len(rows) == 2 * (1 + 4 + 1)                          # per patient: fixed + 4 specialists + coordinator
    assert summary["risk"]["required tools"] == "100%" and summary["risk"]["stage correct"] == "100%"
    assert summary["coordinator"]["agentic (not fallback)"] == "100%"
    assert summary["fixed pipeline"]["completed"] == "100%"
    assert "# Agent evaluation" in ev.markdown(summary, {"date": "x", "brains": ["b"], "n_patients": 2, "repeat": 1, "n_runs": len(rows)})


def test_skip_coordinator_option():
    rows, summary = ev.run_eval(["DEMO-001"], brain_mode="scripted", skip_coordinator=True)
    assert "coordinator" not in summary and len(rows) == 5


class SkipsDiet:
    name = "skips-diet"
    def next_step(self, goal, history, tools, system=None):
        asked = [h["args"].get("role") for h in history]
        for rid in ("clinical_data", "risk", "management"):
            if rid not in asked:
                return {"type": "tool_call", "name": "ask_agent", "args": {"role": rid, "task": ""}}
        parts = [h["result"]["answer"] for h in history if h["result"].get("answer")]
        return {"type": "final", "text": "\n".join(parts)}


def test_a_coordinator_that_skips_a_needed_specialist_is_counted():
    t = ev.truth("DEMO-001")
    row = ev.eval_coordinator("DEMO-001", lambda r: SkipsDiet() if r.id == "coordinator" else ScriptedRoleBrain(r), t)
    assert row["mode"] == "agentic" and row["missed"] == ["diet"] and row["right_specialists"] is False


class Offline:
    name = "offline"
    def next_step(self, goal, history, tools, system=None):
        raise ConnectionError("model server not running")


def test_a_failing_model_is_counted_as_failure_not_hidden():
    t = ev.truth("DEMO-001")
    r = ev.eval_role("DEMO-001", "risk", lambda role: Offline(), t)
    assert r["finished"] is False and r["verified"] is False and r["stage_ok"] is False
    c = ev.eval_coordinator("DEMO-001", lambda role: Offline(), t)
    assert c["mode"] == "fallback" and c["verified"] is False and c["right_specialists"] is False


def test_output_files_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval_agents.py", "--brain", "scripted", "--patients", "DEMO-001", "--skip-coordinator", "--out-dir", str(tmp_path)])
    ev.main()
    data = json.loads((tmp_path / "eval_results.json").read_text(encoding="utf-8"))
    assert data["meta"]["n_patients"] == 1 and (tmp_path / "eval_summary.md").exists()