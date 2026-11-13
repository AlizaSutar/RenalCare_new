"""A2A SERVER: publishes one specialist agent (agentic/roles.py) so other agents can call it over HTTP.

    python -m a2a_agents.server              all four specialists, ports 9101 to 9104
    python -m a2a_agents.server risk         only the risk agent

What each server offers:
  * an AGENT CARD at  /.well-known/agent-card.json   (name, what it does, how to call it)
  * a JSON-RPC endpoint at  /   that accepts a task:  {"study_id": "DEMO-001", "task": "optional short note"}
  * the reply is a Task whose artifact is JSON text: {agent, answer, verified, tools_used, passages, flags}
The server runs the SAME run_specialist() code as the in-process mode, so tools, verifier and logging are unchanged.
"""
import asyncio
import json
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import a2a.helpers as h
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import db
from agentic.roles import ROLES
from agentic.tools import run_specialist
from . import config


def build_card(role, base_url):
    return AgentCard(
        name=role.name, description=role.goal, version="1.0",
        supported_interfaces=[AgentInterface(url=base_url + "/", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"], default_output_modes=["application/json"],
        skills=[AgentSkill(id=role.id, name=role.name, description=role.goal, tags=["ckd", "kdigo", "clinical-decision-support"],
                           examples=['{"study_id": "DEMO-001", "task": ""}'])])


class RoleExecutor(AgentExecutor):
    """Turns an A2A task into one run of a specialist agent."""

    def __init__(self, role):
        self.role = role

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task or h.new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)                          # A2A rule: publish the Task before any status update
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        await u.start_work()
        try:
            req = json.loads(h.get_message_text(context.message))
            sid = str(req["study_id"]).strip().upper()
            note = str(req.get("task", ""))[:200]
            upstream_context = req.get("upstream_context") or {}

            if not isinstance(upstream_context, dict):
                raise ValueError("upstream_context must be a JSON object")
        except Exception:
            await u.failed(u.new_agent_message([h.new_text_part("Send JSON text like {\"study_id\": \"DEMO-001\", \"task\": \"\"}.")]))
            return
        if not config.STUDY_ID.match(sid) or not await asyncio.to_thread(db.one, "SELECT 1 FROM patients WHERE study_id=%s", (sid,)):
            await u.failed(u.new_agent_message([h.new_text_part("Unknown patient.")]))
            return
        # result = await asyncio.to_thread(run_specialist, self.role.id, sid, note)
        
        result = await asyncio.to_thread(
            run_specialist,
            self.role.id,
            sid,
            note,
            upstream_context=upstream_context
        )      # blocking work off the event loop
        if "error" in result:
            await u.failed(u.new_agent_message([h.new_text_part(result["error"])]))
            return
        await u.add_artifact([h.new_text_part(json.dumps(result, ensure_ascii=False))], name="specialist_result",
                             metadata={"media_type": "application/json"})
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass                                                           # runs are short; nothing to cancel


class TokenGate(BaseHTTPMiddleware):
    """If CKD_A2A_TOKEN is set, calls must carry it. The Agent Card stays public (that is how agents find each other)."""

    async def dispatch(self, request, call_next):
        want = config.token()
        if want and request.method != "GET" and request.headers.get("authorization") != f"Bearer {want}":
            return JSONResponse({"error": "missing or wrong A2A token"}, status_code=401)
        return await call_next(request)


def create_app(role_id, base_url=None):
    role = ROLES[role_id]
    card = build_card(role, base_url or config.url(role_id))
    handler = DefaultRequestHandler(agent_executor=RoleExecutor(role), task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/"))
    app.add_middleware(TokenGate)
    return app


def make_server(role_id, port=None, host=None):
    port = port or config.port(role_id)
    host = host or config.HOST
    app = create_app(role_id, base_url=f"http://{host}:{port}")
    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))


async def serve(role_ids):
    servers = [make_server(r) for r in role_ids]
    for r in role_ids:
        print(f"A2A agent '{r}' at {config.url(r)}   card: {config.url(r)}/.well-known/agent-card.json")
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(ROLES)
    bad = [r for r in wanted if r not in ROLES]
    if bad:
        sys.exit(f"Unknown agent(s): {bad}. Choose from: {', '.join(ROLES)}")
    asyncio.run(serve(wanted))