# Working with this Project in Claude Code

This project is configured for [Claude Code](https://claude.com/claude-code). Open it in this directory and the engineering-lead persona, design context, and dev tooling shortcuts are all in place automatically.

## What's configured

| File / dir | Purpose |
|---|---|
| `CLAUDE.md` | Operating manual auto-loaded into every conversation. Lean — points at design docs rather than restating them. Edit when project rules / current status change. |
| `.claude/settings.json` | Project-scoped settings: auto-allowed dev commands (`uv`, `git`, `docker compose`), access to `/home/ubuntu/Dev/pdf-tool` for prototype reads. |
| `.claude/agents/` | Project subagents. Currently: `design-reviewer` (independent architectural review). |
| `.claude/commands/` | Slash commands. Currently: `/run-checks` (lint + type-check + tests in parallel). |
| `~/.claude/projects/-home-ubuntu-Dev-intelligent-workflow-application/memory/` | Auto-memory across sessions: user prefs, feedback, project state, references. Stored under `~/.claude`, not in the repo. |

## How it differs from the prior `.kiro/` setup

Same intent — give the agent durable context and conventions for this project — adjusted for Claude Code's mechanics:

- Static system-prompt content lives in `CLAUDE.md` (always loaded, lean).
- Design docs are referenced, not pre-loaded. Claude reads them when relevant — no token tax on conversations that don't need them.
- Lessons learned across sessions live in the auto-memory system, not in a static prompt that drifts from reality.
- Tool permissions are managed via `.claude/settings.json` rather than per-agent config.
- No keyboard shortcut — Claude Code uses `/agents`, `/clear`, and other slash commands; run `/help` to see them.

## Updating the configuration

| You want to... | Change |
|---|---|
| Update operating rules or project status | Edit `CLAUDE.md`. Keep it short — every line is loaded into every conversation. |
| Add an auto-allowed command | Edit `.claude/settings.json` `permissions.allow`. Use specific patterns (`Bash(uv run pytest:*)`), not wildcards. |
| Add a specialized review or role | Create `.claude/agents/<name>.md` with frontmatter (`name`, `description`, optional `tools`) and a system prompt body. Invoked via the `Agent` tool with `subagent_type: <name>`. |
| Add a canned operation | Create `.claude/commands/<name>.md` with frontmatter (`description`, optional `argument-hint`) and a body. Invoked as `/<name>`. |
| Save a lesson learned across sessions | Add a file under the memory path above and index it in `MEMORY.md`. Memory types: `user`, `feedback`, `project`, `reference`. |

## What's intentionally not configured

- **No subagent for the main work** — that's `CLAUDE.md`, applied to every conversation in this directory.
- **No `skills/` directory yet** — design docs at the project root already serve as on-demand reference. Add skills when there's procedural (workflow-shaped) knowledge that's worth packaging separately, e.g., a "regenerate Bedrock fixtures" runbook.
- **No `additionalDirectories` beyond the prototype** — extend if/when other reference repos get pulled in.

## Sanity check

After cloning, in this directory:

```bash
# Confirm Claude Code sees the config
ls .claude/

# Run the canned dev loop
# (inside Claude Code) /run-checks
```

If any of these don't behave as expected, the config files are the source of truth — edit them.
