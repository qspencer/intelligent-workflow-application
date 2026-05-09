# Specialized Project Agent — Setup Guide

## Overview

This project uses a custom Kiro agent configured as a specialized expert on the Intelligent Workflow Platform. The agent has full context on the architecture, design decisions, implementation plan, and all technical domains involved.

---

## What's Configured

### Agent Configuration

**File:** `.kiro/agents/workflow-platform.json`

Defines:
- Tools available (file ops, search, code intelligence, knowledge base, web)
- Auto-approved tools (read-only operations)
- Resources loaded into every session (all project design docs)
- Skills loaded on demand for deep context
- Keyboard shortcut: `Ctrl+Shift+W`

### System Prompt

**File:** `.kiro/prompts/workflow-platform.txt`

Defines the agent's expertise areas, architectural decisions it enforces, and implementation approach. This is loaded into every conversation and shapes all responses.

### Skills (On-Demand Context)

**Directory:** `.kiro/skills/`

For documents too large to always keep in context, create skill files with YAML frontmatter:

```markdown
---
name: platform-architecture
description: Full architecture document including agent hierarchy, knowledge system, workflow schema, security model, and tech stack. Use when making architectural decisions or implementing core components.
---

[document content here]
```

Skills are loaded only when the agent determines they're relevant to the current task.

---

## First-Time Setup

### 1. Activate the agent

```
/agent workflow-platform
```

Or press `Ctrl+Shift+W`.

### 2. Index project docs as a knowledge base

For semantic search across all project documentation:

```
Index all the markdown files in this directory as a knowledge base called "workflow-platform-design"
```

This enables the agent to search project docs even when they're not all in the active context window.

### 3. Set semantic search mode (recommended)

```bash
kiro-cli settings knowledge.indexType best
```

This uses embedding-based semantic search rather than keyword matching — better for finding conceptually related content.

---

## How the Pieces Work Together

| Mechanism | What it does | When it's used |
|-----------|-------------|----------------|
| System prompt | Defines expertise, enforces decisions, sets coding style | Every message (always in context) |
| Resources | Loads project docs directly into context | Every message (always available) |
| Skills | Loads detailed docs on demand when relevant | Only when the agent needs deep detail |
| Knowledge base | Semantic search across all project content | When the agent needs to find specific information |

---

## Context Budget

The 7 design docs total ~110KB (~27K tokens), using ~13% of a 200K context window. This is acceptable.

**If docs grow significantly**, move larger documents to skills and keep only the most critical (VISION.md, ARCHITECTURE.md) as always-loaded resources.

---

## Updating the Agent

### When design decisions change

Update `.kiro/prompts/workflow-platform.txt` to reflect new decisions the agent should enforce.

### When new docs are added

Add them to the `resources` array in `.kiro/agents/workflow-platform.json`, or create a skill if they're large/specialized.

### When the knowledge base is stale

```
Update the "workflow-platform-design" knowledge base
```

Or re-index:
```
/knowledge update -n "workflow-platform-design" -p ./
```

---

## Switching Between Agents

- `Ctrl+Shift+W` — Toggle to/from the workflow platform agent
- `Shift+Tab` — Toggle to the planning agent (for high-level design work)
- `/agent kiro_default` — Switch back to the general-purpose agent

---

## Directory Structure

```
.kiro/
├── agents/
│   └── workflow-platform.json    — Agent configuration
├── prompts/
│   └── workflow-platform.txt     — System prompt (expertise + decisions)
└── skills/
    └── architecture/
        └── SKILL.md              — On-demand deep architecture context (optional)
```
