# UNITARES Governance Plugin — Design Document

## Goal

Package the UNITARES governance framework as a Claude Code plugin that auto-enrolls agents in governance via hooks, provides focused skills for reference, and includes commands for manual control. Built for personal use first, structured for public distribution later.

## Architecture

A Claude Code plugin (git repo) containing hooks, skills, agents, and commands. The plugin does not bundle the governance MCP server — it connects to one. Users configure the server URL via environment variable. Hooks handle automation (onboard on session start, check-in on every code edit). Skills provide conceptual reference. Commands give manual overrides.

The MCP server itself is separately configured in `~/.claude.json` — this plugin assumes that's already done (and documents how).

---

## Plugin Structure

```
unitares-governance/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── hooks/
│   ├── hooks.json               # SessionStart + PostToolUse config
│   └── session-start            # Bash: auto-onboard, inject EISV
├── agents/
│   └── governance-reviewer.md   # Reviews work against governance state
├── commands/
│   ├── checkin.md               # Manual governance check-in
│   ├── diagnose.md              # Show current EISV + coherence
│   └── dialectic.md             # Request dialectic review
├── skills/
│   ├── governance-fundamentals/
│   │   └── SKILL.md             # EISV theory, basins, verdicts
│   ├── governance-lifecycle/
│   │   └── SKILL.md             # Onboarding, check-ins, recovery
│   ├── dialectic-reasoning/
│   │   └── SKILL.md             # Thesis/antithesis/synthesis
│   ├── knowledge-graph/
│   │   └── SKILL.md             # Search, contribute, update
│   └── discord-bridge/
│       └── SKILL.md             # Discord bridge setup and operation
├── config/
│   └── defaults.env             # Default configuration reference
└── README.md                    # Installation + setup guide
```

---

## Hooks

### SessionStart

Fires on every session start, resume, clear, or compact.

**Behavior:**
1. Read `UNITARES_SERVER_URL` env var (default: `http://localhost:8767`)
2. Ping `/health` endpoint (curl, 3s timeout)
3. If reachable:
   - Derive agent name: `{UNITARES_AGENT_PREFIX}_{project_basename}_{date}`
   - Call `onboard()` via `/v1/tools/call` endpoint
   - Cache UUID + session_id to `.claude/unitares-session.json` in project root
   - Fetch current EISV via `get_governance_metrics()`
   - Return context: current state, verdict, active dialectics
4. If unreachable:
   - Return note: "Governance server offline. Skills available for reference."

**Output format:** JSON with `additional_context` field injected into session prompt.

### PostToolUse (Edit/Write)

Fires after every Edit or Write tool call.

**Behavior:**
1. Read cached session info from `.claude/unitares-session.json`
2. If no cached session: skip (agent wasn't onboarded)
3. Call `process_agent_update()` via `/v1/tools/call` with:
   - `response_text`: filename that was edited
   - `complexity`: 0.3 (default for single-file edits)
   - `confidence`: 0.7 (default)
4. If verdict changes: include verdict in hook output for session awareness
5. On failure: log warning, don't block the edit

**Rate limiting:** The governance ODE handles high-frequency updates. No client-side batching needed.

---

## Skills

### governance-fundamentals

**Trigger:** Agent needs to understand what EISV numbers mean.

**Content:**
- EISV state vector: Energy, Information Integrity, Entropy, Void
- What each dimension measures and how they couple
- Basins: high (healthy), low (degraded), boundary (transitioning)
- Verdicts: proceed, guide, pause, reject
- Coherence: structural health score, range ~0.45-0.55
- Calibration: confidence vs outcomes tracking

### governance-lifecycle

**Trigger:** Agent is interacting with governance for the first time or is stuck/paused.

**Content:**
- How onboarding works (name, UUID, session binding)
- Check-in best practices (when, what to report, honest confidence)
- Reading verdicts and guidance
- Recovery from pause: self-recovery, dialectic review, operator resume
- Identity persistence across sessions

### dialectic-reasoning

**Trigger:** Agent is in a dialectic session or reviewing one.

**Content:**
- Thesis structure: reasoning, root cause, proposed conditions
- Antithesis structure: concerns, observed metrics, counter-reasoning
- Synthesis: negotiating conditions, convergence
- When to request a dialectic review
- How to participate constructively (not defensively)

### knowledge-graph

**Trigger:** Agent discovers something useful or needs existing knowledge.

**Content:**
- Search before creating (avoid duplicates)
- Discovery types: note, insight, bug_found, improvement, analysis, pattern
- Status lifecycle: open → resolved/archived
- Tagging for discoverability
- Closing the loop: update status when resolved

### discord-bridge

**Trigger:** Setting up or operating the Discord integration.

**Content:**
- What the bridge does (8 layers: events, HUD, presence, Lumen, dialectic, knowledge, polls, resonance)
- Autonomous governance (auto-resume, auto-dialectic, neighbor warnings)
- Channel structure and what each channel shows
- Configuration: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, server URLs
- The bridge is read-heavy, write-light — observes governance, acts autonomously on critical events

---

## Agent

### governance-reviewer

**Purpose:** Review current agent state and work quality through a governance lens.

**When dispatched:**
- After completing a major task (via superpowers code-reviewer integration)
- When coherence is dropping or entropy rising
- On request via `/diagnose` command

**Behavior:**
1. Fetch current EISV via `get_governance_metrics()`
2. Fetch recent check-in history
3. Assess:
   - Is coherence trending up or down?
   - Is entropy accumulating?
   - Is the energy/integrity balance healthy (V near zero)?
   - Are there any active dialectics or open findings?
4. Report: green (healthy), yellow (watch), red (needs attention)
5. Recommend: continue, slow down, or request dialectic

**Model:** inherit (uses whatever model the parent session uses)

---

## Commands

### /checkin

```markdown
---
description: "Manual governance check-in with summary, complexity, and confidence"
---

Call the UNITARES governance `process_agent_update` tool with:
- A summary of what you just did (ask the user or derive from recent context)
- Complexity estimate (0.0-1.0)
- Confidence estimate (0.0-1.0, be honest)

Report the verdict and any guidance back to the user.
```

### /diagnose

```markdown
---
description: "Show current EISV state, coherence, risk, and verdict"
---

Call `get_governance_metrics` for the current agent and display:
- EISV values with interpretation
- Coherence score
- Risk score
- Current verdict
- Any active dialectics
- Recent check-in history (last 5)

Format as a readable summary, not raw JSON.
```

### /dialectic

```markdown
---
description: "Request a dialectic review for the current agent"
---

Call `request_dialectic_review` with the current agent ID and ask the user for a reason.
Report the result (session created, reviewers assigned).
```

---

## Configuration

Users configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `UNITARES_SERVER_URL` | `http://localhost:8767` | Governance MCP server URL |
| `UNITARES_AGENT_PREFIX` | `claude` | Prefix for auto-generated agent names |

The plugin README documents how to:
1. Set up the governance MCP server
2. Configure `~/.claude.json` with the MCP server entry
3. Set environment variables
4. Verify connection with `/diagnose`

---

## Distribution

### Phase 1: Private (now)

- GitHub repo: `hikewa/unitares-governance` (private)
- Install locally: clone to `~/.claude/plugins/` or use plugin install

### Phase 2: Public

- Make repo public
- Create marketplace repo: `hikewa/unitares-marketplace` with manifest
- Users install: `/plugin marketplace add hikewa/unitares-marketplace` then `/plugin install unitares-governance`
- README covers: what UNITARES is, why governance matters, how to set up

### Prerequisites for users

- A running UNITARES governance MCP server (self-hosted or shared)
- MCP server configured in `~/.claude.json`
- `UNITARES_SERVER_URL` environment variable (if not localhost)

---

## What This Replaces

| Current | Plugin |
|---------|--------|
| Manual SKILL.md in `~/.claude/skills/` | Bundled as 5 focused skills |
| Manual MCP config in `~/.claude.json` | Documented in README (still manual) |
| Agent manually calls `onboard()` | SessionStart hook auto-onboards |
| Agent manually checks in | PostToolUse hook auto-checks-in |
| MEMORY.md holds governance notes | Skills hold reference, hooks handle state |
| No governance-aware code review | governance-reviewer agent |

---

## Build Sequence

1. Create repo with plugin.json and structure
2. Write SessionStart hook (bash, curl-based)
3. Write PostToolUse hook (bash, curl-based)
4. Split existing SKILL.md into 5 focused skills
5. Write governance-reviewer agent
6. Write 3 commands
7. Write README with setup guide
8. Test locally (install plugin, verify hooks fire, verify auto-onboard)
9. Push to GitHub
