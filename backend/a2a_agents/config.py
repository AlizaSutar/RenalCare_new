"""Where each specialist's A2A server lives. Change with environment variables, no code edits needed.

    CKD_A2A_HOST            default 127.0.0.1   (this computer only. Keep it that way unless you add real authentication.)
    CKD_A2A_<ROLE>_URL      full URL override, for example CKD_A2A_RISK_URL=http://127.0.0.1:9102
    CKD_A2A_TOKEN           optional shared secret. If set, servers refuse calls without it and the client sends it.
    CKD_A2A_TIMEOUT         seconds to wait for one specialist (default 300; a slow local model needs this)
"""
import os
import re

from agentic.roles import ROLES

BASE_PORT = 9100
HOST = os.getenv("CKD_A2A_HOST", "127.0.0.1")
STUDY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_]{1,30}$")        # only ever pass an ID, never free text, into the database


def port(role_id):
    return BASE_PORT + 1 + list(ROLES).index(role_id)


def url(role_id):
    return os.getenv(f"CKD_A2A_{role_id.upper()}_URL") or f"http://{HOST}:{port(role_id)}"


def token():
    return os.getenv("CKD_A2A_TOKEN") or None


def timeout():
    try:
        return float(os.getenv("CKD_A2A_TIMEOUT", "300"))
    except ValueError:
        return 300.0