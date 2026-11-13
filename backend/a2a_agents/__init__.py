"""STEP 5: A2A (Agent2Agent protocol, by Google, library a2a-sdk 1.3.0).

Each of the four specialist agents can be run as its own small A2A server:
    python -m a2a_agents.server                    start all four (ports 9101 to 9104)
    python -m a2a_agents.server risk               start just one
Then switch the coordinator to use them:
    set CKD_A2A=1   (PowerShell: $env:CKD_A2A="1")
If a server is not running, the coordinator falls back to running that specialist in its own process.

What A2A adds here: a standard way for agents to publish what they can do (the Agent Card) and to accept tasks over HTTP,
so an agent written by someone else could be called the same way. What it does NOT change: the tools, the verifier and the
doctor-approval rule are the same code as before.
"""