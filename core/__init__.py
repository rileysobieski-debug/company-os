"""Company OS core engine.

A generic multi-agent business framework. Each company is a folder on disk
(native Obsidian vault folder). The engine reads the company's config and
data from that folder and injects it into every agent's system prompt.

Entry points:
  core.company.CompanyConfig                    — loads config.json / context.md / domain.md
  core.employees.build_workers                  — 4 terminal worker templates, bound to a company
  core.board.convene_board                      — 6-voice advisory debate (no memory, stateless)
  core.managers.load_departments                — auto-discovers dept folders and builds managers
  core.orchestrator.Orchestrator                — top-level coordinator (owns digest + decisions)
  core.wizard.run_wizard                        — new-company interview flow
  core.onboarding.check_and_run_all_onboarding — first-run init for all agent types
  core.meeting.run_department_meeting           — department-level structured discussion
  core.meeting.run_cross_agent_meeting          — cross-department / board meeting
"""
