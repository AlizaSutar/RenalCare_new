"""A2A CLIENT: asks one specialist agent, running as an A2A server, to do its job. Used by agentic/tools.py ask_agent.

Two kinds of failure are kept apart on purpose:
  * the server cannot be reached or refuses us (not running, wrong token, timeout)  -> A2AUnavailable
        ask_agent then falls back to running the specialist in-process, so the app keeps working;
  * the server answered but the specialist could not do the job (unknown patient, model failed) -> {"error": ...}
        the coordinator sees that error and says so. It is NOT hidden by the fallback.
"""
import asyncio
import json
import threading

import httpx

from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

from . import config


class A2AUnavailable(Exception):
    pass


async def _ask(role_id, study_id, task, upstream_context=None):
    headers = {"Authorization": f"Bearer {config.token()}"} if config.token() else {}
    async with httpx.AsyncClient(headers=headers, timeout=config.timeout()) as hc:
        client = await create_client(config.url(role_id), client_config=ClientConfig(streaming=False, httpx_client=hc))
        slim = {k: {"answer": (v.get("answer") or "")[:600], "status": v.get("status"), "verified": v.get("verified")}
                for k, v in (upstream_context or {}).items()}          # trim the content, never the JSON text
        payload = json.dumps({"study_id": study_id, "task": task, "upstream_context": slim}, ensure_ascii=False)
        msg = Message(message_id=f"ckd-{role_id}-{study_id}", role=Role.ROLE_USER, parts=[Part(text=payload)])
        final = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                final = ev.task
        if final is None:
            raise A2AUnavailable("no task came back")
        state = final.status.state
        if state == TaskState.TASK_STATE_COMPLETED:
            for art in final.artifacts:
                for part in art.parts:
                    if part.HasField("text"):
                        return json.loads(part.text)
            raise A2AUnavailable("completed task had no result")
        why = " ".join(p.text for p in final.status.message.parts if p.HasField("text")) if final.status.HasField("message") else ""
        return {"error": f"{role_id} agent (A2A) did not finish: {why or TaskState.Name(state)}"}


def ask_via_a2a(role_id, study_id, task="", upstream_context=None):
    """Blocking call. Returns the specialist's result dict, or {"error": ...}. Raises A2AUnavailable if unreachable."""
    box = {}

    def work():                                    # always in a fresh thread: safe whether or not an event loop is already running
        try:
            box["value"] = asyncio.run(
                _ask(role_id, study_id, task, upstream_context)
            )       
        except A2AUnavailable as e:
            box["error"] = e
        except Exception as e:                      # connection refused, timeout, 401, bad reply ...
            box["error"] = A2AUnavailable(f"{type(e).__name__}: {str(e)[:100]}")

    t = threading.Thread(target=work)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]