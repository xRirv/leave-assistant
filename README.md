# Leave Assistant: an end-to-end leave management agent

Leave Assistant helps students check leave information, submit or withdraw requests, and notify
the HOD through a durable multi-agent workflow.

A student asks a question in plain English. A **supervisor** agent delegates to two **specialist**
agents, an information specialist that can only read and an operations specialist that can change
leave records and notify the HOD. The run is a job on a queue, and a worker that dies halfway
through does not apply the same leave twice.

```
student ─▶ queue (agent.db) ─▶ worker ─▶ supervisor ──ask_leave_info──▶ information agent ─▶ balance, holidays
                                                   └─ask_leave_operations▶ operations agent ─▶ apply, withdraw, notify HOD
                                                                            * side effects: run once per key
```

## Run it (no API key needed)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m scripts.demo            # two questions, scripted models, every step printed
python -m scripts.demo --crash    # the worker dies right after reserving; a second worker finishes: PASS
pytest                            # 21 tests, under a second
```

With Gemini (`export GEMINI_API_KEY=...`):

```bash
python -m scripts.demo --real                                      # same questions, real models
python -m scripts.worker                                           # terminal 1
python -m scripts.ask --student 22CS045 "Do you have anything on operating systems?"   # terminal 2
```

One question costs about 5–7 model calls with three agents, so the free tier runs out quickly.
Use the scripted models for everything except a final check.

## Where each day shows up

| Day | Idea | Where to look |
|---|---|---|
| 1 | The agent loop, self-healing tool errors | `app/agents.py` `run_specialist` |
| 2 | Tool descriptions are prompts; rules live in data; schema | `app/tools/leave_tools.py`, `schema/leave.sql` (`policy` table) |
| 2 | Agent memory apart from business data | `agent.db` vs `leave.db` |
| 3 | A run is a job: queue, lease, heartbeat, reaper | `app/memory.py`, `app/worker.py`, `app/runner.py` |
| 3 | Idempotency keys; safe writes | `LeaveDb.once`, `LeaveDb.apply_leave`, `record_notification` |
| 4 | Supervisor and specialists ("agent as tool") | `app/agents.py` `SupervisorTools` |
| 4 | Least privilege per agent | information has no write tools; operations is bound to one student |
| 4 | Keys passed down to specialists | `run_tool` hands the delegation's key to `run_specialist` |

## Seed data

| Student | Leave balance | Policy | What happens |
|---|---|---|---|
| 22CS045 Priya Raman | 12 days | 10 days per request | Can apply for leave |
| 22IT017 Arjun Kumar | 2 days | 10 days per request | Limited by remaining balance |
| 22EC031 Divya Sekar | 0 days | 10 days per request | Refused: no balance remains |

## Known limits (on purpose, for later days)

- A specialist's inner steps are not stored; only the delegation and its answer are. After a crash the
  specialist runs again, and keys keep its side effects single. Storing them is checkpointing (Day 5).
- Keys only match if the model repeats the same call. The scripted models always do; real models
  usually do at temperature 0. The reservation and text are also safe to repeat on their own, which
  covers the rest.
- No approval step before a side effect (Day 5), no guardrails or metrics (Day 6), no MCP (Day 7).
