# UNITARES Governance Plugin — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a Claude Code plugin that automatically enrolls agents in UNITARES governance via hooks, provides focused skills for reference, and includes commands and an agent for manual control.

**Architecture:** Git repo with `.claude-plugin/plugin.json` metadata, bash hook scripts for SessionStart (auto-onboard) and PostToolUse (auto-checkin), 5 focused skills split from the existing monolithic SKILL.md, 1 agent definition, and 3 commands. No Python runtime — hooks use curl against the governance HTTP API.

**Tech Stack:** Bash (hooks), Markdown (skills/agents/commands), JSON (config), curl (HTTP calls to governance MCP)

**Design Doc:** `docs/plans/2026-02-23-unitares-plugin-design.md`

---

## Task 1: Create repo and plugin metadata

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/.claude-plugin/plugin.json`
- Create: `/Users/cirwel/projects/unitares-governance-plugin/README.md`

**Step 1: Create the repo**

```bash
mkdir -p /Users/cirwel/projects/unitares-governance-plugin
cd /Users/cirwel/projects/unitares-governance-plugin
git init
```

**Step 2: Create plugin.json**

```json
{
  "name": "unitares-governance",
  "description": "Thermodynamic governance for AI agents — auto-onboard, auto-checkin, EISV monitoring, dialectic reasoning, knowledge graph",
  "version": "0.1.0",
  "author": {
    "name": "hikewa"
  },
  "homepage": "https://github.com/hikewa/unitares-governance",
  "repository": "https://github.com/hikewa/unitares-governance",
  "license": "MIT",
  "keywords": ["governance", "eisv", "thermodynamic", "multi-agent", "dialectic", "coherence"]
}
```

**Step 3: Create README.md**

Write a README covering:
- What UNITARES is (2-3 sentences)
- What this plugin does (auto-onboard, auto-checkin, skills, commands)
- Prerequisites (running governance MCP server, MCP configured in ~/.claude.json)
- Installation: `git clone` to local, then plugin install
- Configuration: `UNITARES_SERVER_URL` env var
- Quick start: install, set env var, start a session, run `/diagnose`

**Step 4: Commit**

```bash
git add .claude-plugin/plugin.json README.md
git commit -m "feat: initial plugin skeleton with metadata and README"
```

---

## Task 2: Hook infrastructure — run-hook.cmd and hooks.json

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/hooks/hooks.json`
- Create: `/Users/cirwel/projects/unitares-governance-plugin/hooks/run-hook.cmd`

**Step 1: Create hooks.json**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd' session-start",
            "async": false
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd' post-edit",
            "async": true
          }
        ]
      }
    ]
  }
}
```

Note: PostToolUse is `async: true` so it doesn't block the agent while checking in.

**Step 2: Create run-hook.cmd**

Copy the cross-platform polyglot wrapper from superpowers (it's the same pattern — routes to bash scripts by name). This handles Windows (finds Git Bash) and Unix (exec bash directly).

```bash
: << 'CMDBLOCK'
@echo off
REM Cross-platform polyglot wrapper for hook scripts.
if "%~1"=="" (
    echo run-hook.cmd: missing script name >&2
    exit /b 1
)
set "HOOK_DIR=%~dp0"
if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b %ERRORLEVEL%
)
where bash >nul 2>nul
if %ERRORLEVEL% equ 0 (
    bash "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b %ERRORLEVEL%
)
exit /b 0
CMDBLOCK

# Unix: run the named script directly
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$1"
shift
exec bash "${SCRIPT_DIR}/${SCRIPT_NAME}" "$@"
```

**Step 3: Commit**

```bash
chmod +x hooks/run-hook.cmd
git add hooks/hooks.json hooks/run-hook.cmd
git commit -m "feat: hook infrastructure — hooks.json and cross-platform runner"
```

---

## Task 3: SessionStart hook — auto-onboard

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/hooks/session-start`
- Create: `/Users/cirwel/projects/unitares-governance-plugin/config/defaults.env`

**Step 1: Create defaults.env**

```bash
# UNITARES Governance Plugin — Default Configuration
# Override these in your shell environment or project .env
UNITARES_SERVER_URL=http://localhost:8767
UNITARES_AGENT_PREFIX=claude
```

**Step 2: Create session-start hook script**

This script:
1. Reads `UNITARES_SERVER_URL` (default: `http://localhost:8767`)
2. Pings `/health` with 3s timeout
3. If reachable: calls `onboard()` via `/v1/tools/call`, caches result
4. Fetches EISV via `get_governance_metrics()`
5. Reads the governance-fundamentals skill for context injection
6. Outputs JSON with `additional_context` containing EISV state + skill content
7. If unreachable: outputs context noting governance is offline

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration
SERVER_URL="${UNITARES_SERVER_URL:-http://localhost:8767}"
AGENT_PREFIX="${UNITARES_AGENT_PREFIX:-claude}"
SESSION_CACHE="${PWD}/.claude/unitares-session.json"

# JSON escape helper
escape_for_json() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# Try to reach governance server
health_response=$(curl -s -m 3 "${SERVER_URL}/health" 2>/dev/null || echo "UNREACHABLE")

if [ "$health_response" = "UNREACHABLE" ] || [ -z "$health_response" ]; then
    # Server offline — inject reference-only context
    skill_content=$(cat "${PLUGIN_ROOT}/skills/governance-fundamentals/SKILL.md" 2>/dev/null || echo "Governance skill not found")
    skill_escaped=$(escape_for_json "$skill_content")
    context="UNITARES governance server is offline (${SERVER_URL}). Skills are available for reference but state operations will fail.\\n\\n${skill_escaped}"
    cat <<EOF
{
  "additional_context": "${context}",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF
    exit 0
fi

# Server is reachable — auto-onboard
project_name=$(basename "${PWD}")
date_stamp=$(date +%Y%m%d)
agent_name="${AGENT_PREFIX}_${project_name}_${date_stamp}"

# Call onboard
onboard_response=$(curl -s -m 10 -X POST "${SERVER_URL}/v1/tools/call" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"onboard\", \"arguments\": {\"name\": \"${agent_name}\", \"model_type\": \"claude-code\"}}" \
    2>/dev/null || echo "{}")

# Extract agent_id and client_session_id from response
# MCP wraps: result.content[0].text is a JSON string
agent_id=$(echo "$onboard_response" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    inner = r.get('result', r)
    content = inner.get('content', [])
    if content:
        data = json.loads(content[0].get('text', '{}'))
    else:
        data = inner
    print(data.get('agent_id', data.get('uuid', '')))
except: print('')
" 2>/dev/null || echo "")

session_id=$(echo "$onboard_response" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    inner = r.get('result', r)
    content = inner.get('content', [])
    if content:
        data = json.loads(content[0].get('text', '{}'))
    else:
        data = inner
    print(data.get('client_session_id', ''))
except: print('')
" 2>/dev/null || echo "")

# Cache session info
if [ -n "$agent_id" ]; then
    mkdir -p "$(dirname "$SESSION_CACHE")"
    cat > "$SESSION_CACHE" <<CACHE
{
  "agent_id": "${agent_id}",
  "client_session_id": "${session_id}",
  "server_url": "${SERVER_URL}",
  "agent_name": "${agent_name}",
  "onboarded_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
CACHE
fi

# Fetch current EISV metrics
metrics_response=""
if [ -n "$agent_id" ]; then
    metrics_response=$(curl -s -m 5 -X POST "${SERVER_URL}/v1/tools/call" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"get_governance_metrics\", \"arguments\": {\"agent_id\": \"${agent_id}\"}}" \
        2>/dev/null || echo "")
fi

# Parse metrics for context
eisv_summary=$(echo "$metrics_response" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    inner = r.get('result', r)
    content = inner.get('content', [])
    if content:
        data = json.loads(content[0].get('text', '{}'))
    else:
        data = inner
    E = data.get('E', 0)
    I = data.get('I', 0)
    S = data.get('S', 0)
    V = data.get('V', 0)
    verdict = data.get('verdict', 'unknown')
    coherence = data.get('coherence', 0)
    risk = data.get('risk_score', 0)
    print(f'EISV: E={E:.2f} I={I:.2f} S={S:.2f} V={V:.2f} | Verdict: {verdict} | Coherence: {coherence:.3f} | Risk: {risk:.2f}')
except: print('EISV: unavailable')
" 2>/dev/null || echo "EISV: unavailable")

# Read governance skill for context
skill_content=$(cat "${PLUGIN_ROOT}/skills/governance-fundamentals/SKILL.md" 2>/dev/null || echo "")
skill_escaped=$(escape_for_json "$skill_content")

# Build context
context="UNITARES governance active. Agent: ${agent_name} (${agent_id:0:8}...). ${eisv_summary}\\n\\nSession ID: ${session_id}\\nServer: ${SERVER_URL}\\n\\nThe PostToolUse hook auto-checks-in after every Edit/Write. Use /diagnose for full state, /checkin for manual check-in, /dialectic to request review.\\n\\n${skill_escaped}"

cat <<EOF
{
  "additional_context": "${context}",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF

exit 0
```

**Step 3: Commit**

```bash
chmod +x hooks/session-start
git add hooks/session-start config/defaults.env
git commit -m "feat: SessionStart hook — auto-onboard with EISV context injection"
```

---

## Task 4: PostToolUse hook — auto-checkin

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/hooks/post-edit`

**Step 1: Create post-edit hook script**

This script:
1. Reads cached session from `.claude/unitares-session.json`
2. If no session: exits silently (not onboarded)
3. Calls `process_agent_update()` with the tool input (filename from the Edit/Write)
4. If verdict changed: includes it in output for session awareness
5. On failure: exits silently (don't block edits)

```bash
#!/usr/bin/env bash
# PostToolUse hook — auto-checkin after Edit/Write
# Runs async (doesn't block the agent)

set -uo pipefail

SESSION_CACHE="${PWD}/.claude/unitares-session.json"

# Skip if no session cached
if [ ! -f "$SESSION_CACHE" ]; then
    exit 0
fi

# Read session info
server_url=$(python3 -c "
import json
with open('${SESSION_CACHE}') as f:
    print(json.load(f).get('server_url', ''))
" 2>/dev/null || echo "")

session_id=$(python3 -c "
import json
with open('${SESSION_CACHE}') as f:
    print(json.load(f).get('client_session_id', ''))
" 2>/dev/null || echo "")

if [ -z "$server_url" ] || [ -z "$session_id" ]; then
    exit 0
fi

# Read hook input from stdin (Claude passes tool info as JSON)
tool_input=$(cat)
tool_name=$(echo "$tool_input" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Extract filename from the tool input
    ti = data.get('tool_input', {})
    print(ti.get('file_path', ti.get('path', 'unknown file')))
except: print('code edit')
" 2>/dev/null || echo "code edit")

# Call process_agent_update
curl -s -m 5 -X POST "${server_url}/v1/tools/call" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"process_agent_update\",
        \"arguments\": {
            \"response_text\": \"Edited: ${tool_name}\",
            \"complexity\": 0.3,
            \"confidence\": 0.7,
            \"client_session_id\": \"${session_id}\"
        }
    }" >/dev/null 2>&1 || true

exit 0
```

**Step 2: Commit**

```bash
chmod +x hooks/post-edit
git add hooks/post-edit
git commit -m "feat: PostToolUse hook — auto-checkin on every Edit/Write"
```

---

## Task 5: Governance Fundamentals skill

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/skills/governance-fundamentals/SKILL.md`

**Step 1: Write the skill**

Extract the "Core Concepts" section from the existing SKILL.md — EISV, basins, verdicts, coherence, calibration. This is the reference material agents need to understand what the numbers mean.

Frontmatter:
```yaml
---
name: governance-fundamentals
description: >
  Use when an agent needs to understand UNITARES governance concepts — EISV state vectors,
  basins, verdicts, coherence, calibration. Reference material for interpreting governance
  metrics and understanding the thermodynamic model.
---
```

Content: The EISV section, basins and verdicts, coherence, calibration, and the "What NOT to Do" section from the existing SKILL.md. Include the Lumen sensor mappings note.

**Step 2: Commit**

```bash
git add skills/governance-fundamentals/SKILL.md
git commit -m "feat: governance-fundamentals skill — EISV, basins, verdicts, coherence"
```

---

## Task 6: Governance Lifecycle skill

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/skills/governance-lifecycle/SKILL.md`

**Step 1: Write the skill**

Extract the "How to Work as an Agent" section — starting a session, check-ins, reading feedback, identity. Add recovery guidance (self_recovery, dialectic review, operator resume).

Frontmatter:
```yaml
---
name: governance-lifecycle
description: >
  Use when an agent is interacting with UNITARES governance for the first time, needs to
  onboard, check in, or recover from a pause/reject verdict. Covers the full agent lifecycle
  from session start through check-ins to recovery.
---
```

Content: Session start, check-in best practices, reading verdicts, identity persistence, recovery from pause (self_recovery tool, dialectic request, operator resume). Include the MCP Tools Reference section listing essential/common/specialized tools.

**Step 2: Commit**

```bash
git add skills/governance-lifecycle/SKILL.md
git commit -m "feat: governance-lifecycle skill — onboarding, check-ins, recovery"
```

---

## Task 7: Dialectic Reasoning skill

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/skills/dialectic-reasoning/SKILL.md`

**Step 1: Write the skill**

Cover the dialectic protocol in depth — not just what it is, but how to participate effectively.

Frontmatter:
```yaml
---
name: dialectic-reasoning
description: >
  Use when an agent is participating in a UNITARES dialectic session — paused and needs to
  submit a thesis, reviewing another agent's thesis, or synthesizing conditions for resolution.
  Covers structured argumentation and convergence.
---
```

Content:
- When dialectics happen (pause/reject, manual request, disagreement)
- Thesis structure: reasoning, root cause analysis, proposed conditions
- Antithesis structure: concerns, observed metrics, counter-reasoning
- Synthesis: negotiating conditions, when to agree vs push back
- The tools: `request_dialectic_review()`, `submit_thesis()`, `submit_antithesis()`, `submit_synthesis()`
- Common mistakes (being defensive, ignoring metrics, proposing impossible conditions)

**Step 2: Commit**

```bash
git add skills/dialectic-reasoning/SKILL.md
git commit -m "feat: dialectic-reasoning skill — structured argumentation and resolution"
```

---

## Task 8: Knowledge Graph skill

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/skills/knowledge-graph/SKILL.md`

**Step 1: Write the skill**

Frontmatter:
```yaml
---
name: knowledge-graph
description: >
  Use when an agent needs to search the shared knowledge graph, contribute a discovery,
  or update existing entries. Covers search, tagging, discovery types, and status lifecycle.
---
```

Content:
- Search before creating (avoid duplicates)
- `search_knowledge_graph()` — semantic and tag-based
- `leave_note()` — quick contribution
- `knowledge()` — full CRUD (store, update, details, cleanup)
- Discovery types: note, insight, bug_found, improvement, analysis, pattern
- Status lifecycle: open → resolved/archived
- Tagging best practices
- Closing the loop: always update status when resolved

**Step 2: Commit**

```bash
git add skills/knowledge-graph/SKILL.md
git commit -m "feat: knowledge-graph skill — search, contribute, update discoveries"
```

---

## Task 9: Discord Bridge skill

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/skills/discord-bridge/SKILL.md`

**Step 1: Write the skill**

Frontmatter:
```yaml
---
name: discord-bridge
description: >
  Use when setting up or operating the UNITARES Discord bridge — a standalone bot that
  surfaces governance events, agent presence, Lumen's state, and autonomous governance
  actions as a living Discord server.
---
```

Content:
- What the bridge does (8 layers: events, HUD, presence, Lumen, dialectic, knowledge, polls, resonance)
- Autonomous governance (auto-resume, auto-dialectic, neighbor warnings)
- Channel structure (5 categories, 13+ channels, 2 forums)
- Configuration (DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, GOVERNANCE_MCP_URL, ANIMA_MCP_URL)
- Architecture (polling, not webhooks; read-heavy, write-light except autonomous actions)
- How to run: `pip install -e . && python -m bridge.bot`
- Repo location: `unitares-discord-bridge`

**Step 2: Commit**

```bash
git add skills/discord-bridge/SKILL.md
git commit -m "feat: discord-bridge skill — setup and operation guide"
```

---

## Task 10: Governance Reviewer agent

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/agents/governance-reviewer.md`

**Step 1: Write the agent definition**

```yaml
---
name: governance-reviewer
description: |
  Use this agent when a major task has been completed and you want to review the agent's
  governance health. Examples: <example>Context: An agent just finished implementing a feature.
  user: "I've completed the authentication module" assistant: "Let me check your governance
  state to make sure you're in a healthy basin." <commentary>After significant work, dispatch
  the governance-reviewer to assess EISV health and recommend next steps.</commentary></example>
model: inherit
---
```

Agent prompt content:
- You are a governance health reviewer
- Fetch EISV via `get_governance_metrics()`
- Assess: coherence trending, entropy accumulation, E/I balance, void drift
- Check for active dialectics or open high-severity findings
- Report: green (healthy, continue), yellow (watch, slow down), red (needs attention, consider dialectic)
- Be concise — 3-5 lines max unless something is wrong

**Step 2: Commit**

```bash
git add agents/governance-reviewer.md
git commit -m "feat: governance-reviewer agent — EISV health assessment"
```

---

## Task 11: Commands — /checkin, /diagnose, /dialectic

**Files:**
- Create: `/Users/cirwel/projects/unitares-governance-plugin/commands/checkin.md`
- Create: `/Users/cirwel/projects/unitares-governance-plugin/commands/diagnose.md`
- Create: `/Users/cirwel/projects/unitares-governance-plugin/commands/dialectic.md`

**Step 1: Create /checkin command**

```markdown
---
description: "Manual governance check-in — report what you did, complexity, and confidence to UNITARES"
---

Call the UNITARES process_agent_update tool with:
- response_text: A brief summary of what was just accomplished (derive from recent context or ask)
- complexity: Estimate 0.0-1.0 of how difficult the work was
- confidence: Estimate 0.0-1.0 of how confident you are in the output (be honest — overconfidence is tracked)

Report the verdict and any guidance back. If the verdict is "guide", read and follow the guidance. If "pause", stop and consider requesting a dialectic review.
```

**Step 2: Create /diagnose command**

```markdown
---
description: "Show current UNITARES governance state — EISV, coherence, risk, verdict"
---

Call get_governance_metrics with include_state=true for the current agent. Display:
- EISV values (E, I, S, V) with brief interpretation of each
- Coherence score
- Risk score
- Current verdict
- Basin (high/low/boundary)
- Any active dialectic sessions

Format as a clean, readable summary. Not raw JSON.
```

**Step 3: Create /dialectic command**

```markdown
---
description: "Request a UNITARES dialectic review for the current agent"
---

Call request_dialectic_review for the current agent. Ask for a brief reason if one wasn't provided. Report the result — whether a session was created and who the reviewers are.
```

**Step 4: Commit**

```bash
git add commands/checkin.md commands/diagnose.md commands/dialectic.md
git commit -m "feat: /checkin, /diagnose, /dialectic commands"
```

---

## Task 12: Local installation and testing

**Step 1: Verify directory structure**

```bash
cd /Users/cirwel/projects/unitares-governance-plugin
find . -type f | sort
```

Expected:
```
./.claude-plugin/plugin.json
./README.md
./agents/governance-reviewer.md
./commands/checkin.md
./commands/diagnose.md
./commands/dialectic.md
./config/defaults.env
./hooks/hooks.json
./hooks/post-edit
./hooks/run-hook.cmd
./hooks/session-start
./skills/dialectic-reasoning/SKILL.md
./skills/discord-bridge/SKILL.md
./skills/governance-fundamentals/SKILL.md
./skills/governance-lifecycle/SKILL.md
./skills/knowledge-graph/SKILL.md
```

**Step 2: Test SessionStart hook manually**

```bash
cd /Users/cirwel/projects/unitares-governance-plugin
UNITARES_SERVER_URL=http://localhost:8767 bash hooks/session-start
```

Expected: JSON output with `additional_context` containing EISV state and skill content. If governance is down: JSON with "offline" note.

**Step 3: Test PostToolUse hook manually**

```bash
cd /Users/cirwel/projects/unitares-governance-plugin
# Create a fake session cache
mkdir -p .claude
echo '{"agent_id":"test","client_session_id":"test","server_url":"http://localhost:8767"}' > .claude/unitares-session.json
echo '{"tool_input":{"file_path":"test.py"}}' | bash hooks/post-edit
```

Expected: No output (async, silent). Check governance logs to verify a check-in was received.

**Step 4: Install plugin locally**

The exact installation method depends on Claude Code's plugin install command. Try:
```bash
# Option A: symlink into plugins cache
ln -s /Users/cirwel/projects/unitares-governance-plugin /Users/cirwel/.claude/plugins/cache/local/unitares-governance/0.1.0

# Option B: use claude plugin install if available
```

**Step 5: Start a new Claude Code session and verify**

1. SessionStart hook fires → agent is onboarded → EISV context injected
2. Edit a file → PostToolUse hook fires → check-in sent to governance
3. Run `/diagnose` → shows current EISV state
4. Run `/checkin` → manual check-in works
5. Skills are discoverable via `Skill` tool

**Step 6: Commit any fixes from testing**

```bash
git add -A
git commit -m "fix: adjustments from local testing"
```

---

## Task 13: Push to GitHub

**Step 1: Create the repo on GitHub**

```bash
gh repo create hikewa/unitares-governance --private --source=/Users/cirwel/projects/unitares-governance-plugin --push
```

**Step 2: Verify**

```bash
gh repo view hikewa/unitares-governance
```

---

## Summary

| Task | What's Done After |
|------|------------------|
| 1 | Repo exists with plugin.json and README |
| 2 | Hook infrastructure (hooks.json, run-hook.cmd) |
| 3 | SessionStart auto-onboard hook |
| 4 | PostToolUse auto-checkin hook |
| 5 | governance-fundamentals skill |
| 6 | governance-lifecycle skill |
| 7 | dialectic-reasoning skill |
| 8 | knowledge-graph skill |
| 9 | discord-bridge skill |
| 10 | governance-reviewer agent |
| 11 | 3 commands (/checkin, /diagnose, /dialectic) |
| 12 | Locally tested and working |
| 13 | Pushed to GitHub |
