> Living document. Update this, don't create new versions.

# Ralph Loop Use Cases

Brainstormed use cases for multi-phase iterative loop tasks. These follow the "phased analysis" pattern where each iteration tackles a different dimension of the problem, building on prior iteration reviews.

## Patterns That Make Loops Shine

1. **Progressive enrichment** — Collect raw data, then enrich across passes (add age data, correlate with events, cross-reference sources)
2. **Dimension-per-iteration** — Each pass covers a different analysis dimension, final pass cross-references all of them
3. **Inventory > Analyze > Recommend** — Build the full picture, drill in, produce actionable output
4. **Draft > Refine > Polish** — Each pass improves prose quality and categorization

## Use Cases

| # | Use Case | Iterations | Output | Scope |
|---|----------|-----------|--------|-------|
| 1 | Codebase Health Dashboard | 8 | HTML dashboard | Any project |
| 2 | Dependency Vulnerability Audit | 10 | HTML report | Any project |
| 3 | API Surface Documenter | 6 | HTML/MD docs | Any project |
| 4 | Session Efficiency Analyzer | 5 | HTML analytics | AgentWire internal |
| 5 | TODO/FIXME Debt Tracker | 5 | HTML report | Any project |
| 6 | Documentation Drift Detector | 6 | HTML report | Any project |
| 7 | Competitive/Ecosystem Monitor | 5 | HTML brief | AgentWire internal |
| 8 | Changelog Drafter | 5 | Markdown | Any project |
| 9 | Test Gap Finder | 8 | HTML + stub files | Any project |
| 10 | Log Pattern Analyzer | 5 | HTML report | Any project |
| 11 | Architecture Conformance Checker | 6 | HTML report | Any project |
| 12 | Cross-Project Dependency Mapper | 5 | HTML graph | Any project |
| 13 | Security Posture Scanner | 6 | HTML report | Any project |
| 14 | Release Readiness Checklist | 5 | HTML checklist | Any project |
| 15 | Worker Performance Profiler | 5 | HTML analytics | AgentWire internal |
| 16 | Stale Branch/PR Janitor Report | 4 | HTML report | Any project |
| 17 | Prompt/Role Quality Auditor | 5 | HTML report | AgentWire internal |
| 18 | Migration Impact Analyzer | 6 | Markdown plan | Any project |
| 19 | Config Consistency Checker | 4 | HTML report | AgentWire internal |
| 20 | Daily Standup Drafter | 4 | Markdown + voice | Any project |

## Selected Details

### 5. TODO/FIXME Debt Tracker (Progressive Enrichment)
- Iter 1: Collect all TODO/FIXME/HACK/XXX comments
- Iter 2: Run git blame to determine age of each
- Iter 3: Cluster by module
- Iter 4: Produce prioritized action plan
- Pre-commands: none needed, reads codebase directly

### 11. Architecture Conformance Checker (Dimension-per-iteration)
- Iter 1: Extract rules from CLAUDE.md
- Iter 2: Check import patterns
- Iter 3: Check file organization
- Iter 4: Check naming conventions
- Iter 5-6: Cross-reference and produce conformance report

### 20. Daily Standup Drafter (Draft > Refine > Polish)
- Iter 1: Gather raw data (git log, PR list, session history)
- Iter 2: Categorize by project/feature
- Iter 3: Identify blockers from failed sessions
- Iter 4: Produce polished standup draft
- Pre-commands: `git log --since="yesterday" --oneline`, `gh pr list --author @me --json title,state`

## Notes

- These are all **finite, phased** loop tasks (not infinite daemons)
- For infinite daemon-style loops (content generation, monitoring), use `loop_delay` to pace iterations
- Most output to `~/.agentwire/artifacts/` as HTML dashboards
- Several use pre-commands to gather initial data before the loop starts
