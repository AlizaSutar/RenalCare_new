"""STEP 3: ROLES. This is what turns one general loop into four separate agents.

An agent is not a different program. It is the SAME loop (loop.py) given:
    * its own GOAL           - what it is asked to do
    * its own SYSTEM PROMPT  - how it must behave
    * its OWN TOOL LIST      - the only tools it is allowed to use (the allow-list)

Why a separate tool list matters: the Diet agent physically cannot call get_care_gaps, even if
the model wants to. That is a safety rule enforced by code, not by asking the model nicely.

The four roles match the project workflow (same names as orchestrator.py):
    clinical_data  ->  risk  ->  management  ->  diet
"""
from dataclasses import dataclass

RULES = """Rules you must follow:
- Use ONLY your tools. Never do arithmetic, staging or risk grading yourself. Quote what the tools return.
- Every clinical statement must carry a citation in square brackets copied from a tool result,
  for example a guideline page [KDIGO 2024 CKD S197] or a care-gap source. If you have no source, say "Insufficient evidence".
- You only SUGGEST for the doctor to review. Never say you changed, ordered, stopped or prescribed anything.
- Plain language, under 150 words."""


@dataclass
class Role:
    id: str                    # short id used in the URL and in tests
    name: str                  # saved in agent_runs.agent (matches orchestrator.NAMES)
    goal: str                  # the task given to the brain
    system: str                # how the brain must behave
    tools: list                # allow-list of tool names
    max_steps: int = 6
    default_query: str = ""    # used ONLY by the scripted test brain for search_guidelines


ROLES = {r.id: r for r in [
    Role("clinical_data", "Clinical Data Agent",
         "Check which key values are on record for this patient and which are missing or stale.",
         "You are the Clinical Data Agent in a kidney (CKD) clinic. Your only job is to report data completeness: "
         "what is on record, what is missing, what is out of date. Call check_data_completeness, then summarise.\n" + RULES,
         ["check_data_completeness"], max_steps=3),

    Role("risk", "Risk Assessment Agent",
         "Report the patient's KDIGO stage and risk, or the screening result if there are no kidney labs.",
         "You are the Risk Assessment Agent. Call assess_risk and report the KDIGO stage, risk tier, eGFR trend and "
         "monitoring interval exactly as returned. If the tool says the risk cannot be graded, say why.\n" + RULES,
         ["assess_risk"], max_steps=3),

    Role("management", "Clinical Management Agent",
         "List the open care gaps and find the guideline passage that supports the most important one.",
         "You are the Clinical Management Agent. Call assess_risk, then get_care_gaps, then search_guidelines "
         "for the most important care gap. Write the points for the doctor to review, each with its source.\n" + RULES,
         ["assess_risk", "get_care_gaps", "search_guidelines"], max_steps=6,
         default_query="chronic kidney disease monitoring"),

    Role("diet", "Diet Recommendation Agent",
         "Find guideline passages on diet that fit this patient's conditions, for the doctor and a dietitian to review.",
         "You are the Diet Recommendation Agent. Call get_patient_summary and check_data_completeness to see the conditions "
         "and potassium, then search_guidelines for diet topics that apply (protein, sodium, potassium, diabetes). "
         "You do not prescribe a diet. A dietitian individualises it.\n" + RULES,
         ["get_patient_summary", "check_data_completeness", "search_guidelines"], max_steps=6,
         default_query="protein intake CKD"),
]}


# The coordinator is kept OUT of ROLES on purpose: ask_agent can only start the four specialists, never another coordinator.
COORDINATOR = Role(
    "coordinator", "Coordinator Agent",
    "Prepare a combined, source-based summary for the doctor by asking the right specialist agents.",
    "You are the Coordinator of a kidney (CKD) clinic decision-support team. You never look at patient data yourself. "
    "Your only tool is ask_agent(role, task), which asks one specialist: clinical_data, risk, management, diet.\n"
    "Decide which specialists are needed:\n"
    "- Ask clinical_data first, then risk.\n"
    "- Ask management if the patient has kidney results (a KDIGO stage) or the screening result is watch or high.\n"
    "- Ask diet only if a KDIGO stage or a watch/high screening result was reported. Skip it otherwise.\n"
    "- Ask each specialist at most once. Do not ask for anything not listed.\n"
    "Then write ONE combined summary for the doctor. Copy each specialist's findings and their citations in square brackets "
    "exactly. Add no new facts and no numbers of your own. Say which specialists you skipped and why.\n" + RULES,
    ["ask_agent"], max_steps=6)
