# Intelligent Workflow Platform — Generative UI

## Concept

There is no fixed interface. The UI is a blank canvas that the system populates based on user requests. Users describe what they want to see and where, and the system generates it in real time.

```
User: "Show me a graph of emails processed per hour, updated every 30 seconds.
       Put that in the top left."

User: "Add a list of failed workflows from today in the bottom half."

User: "When something fails, flash that list red for 5 seconds."

User: "Actually, make the graph smaller and add a cost-per-day number next to it."
```

The result is a fully personalized, living interface that each user builds through conversation.

---

## Why This Approach

| Traditional UI | Generative UI |
|---------------|---------------|
| Designer decides what's on screen | User decides what's on screen |
| Same layout for everyone | Every user's view is unique |
| Fixed widgets, fixed positions | Any visualization, any position, any behavior |
| Settings buried in menus | "Show me X" / "Hide Y" |
| Requires learning the interface | Requires describing what you want |
| Static until next release | Evolves continuously per user |
| Overwhelming (shows everything) | Minimal (shows only what's asked for) |

---

## How It Works

### The UI Agent

A dedicated agent (or capability of the orchestrator) that translates natural language into UI components:

```
User request
     │
     ▼
UI Agent interprets intent
     │
     ▼
Generates component specification (what, where, data source, refresh rate, behavior)
     │
     ▼
Frontend renders the component
     │
     ▼
WebSocket keeps it live
```

### Component Specification

The UI agent produces a component spec that the frontend knows how to render:

```json
{
  "id": "emails-per-hour-chart",
  "type": "line_chart",
  "position": { "area": "top-left", "width": "40%", "height": "30%" },
  "data_source": {
    "query": "SELECT hour, count FROM workflow_metrics WHERE type='email' GROUP BY hour",
    "refresh_interval_seconds": 30
  },
  "config": {
    "title": "Emails Processed Per Hour",
    "x_axis": "hour",
    "y_axis": "count",
    "color": "blue"
  },
  "behaviors": []
}
```

### Component Types

The system can generate any of these on demand:

| Type | Use Case |
|------|----------|
| `line_chart` | Trends over time (volume, cost, errors) |
| `bar_chart` | Comparisons (workflows by type, spend by team) |
| `number` | Single metric (total cost today, active workflows) |
| `table` | Lists (failed workflows, pending approvals, recent activity) |
| `status_indicator` | System health, workflow state (green/yellow/red) |
| `live_feed` | Streaming events (agent activity, log entries) |
| `text_block` | Natural language summaries (orchestrator status report) |
| `form` | Input collection (approve/reject, provide data, answer agent question) |
| `button` | Trigger action (start workflow, retry, pause) |
| `map` | Geographic data if relevant |
| `timeline` | Workflow execution history, step progression |
| `tree` | Workflow graph, agent hierarchy |
| `notification_area` | Alerts, escalations, suggestions |
| `conversation` | Chat interface for interacting with the system |

### Positioning

Users describe position naturally. The system maps to a grid:

| User says | System interprets |
|-----------|-------------------|
| "top left" | Grid area: row 1, col 1 |
| "bottom half" | Grid area: rows 3-4, cols 1-4 |
| "next to the chart" | Adjacent to the referenced component |
| "make it bigger" | Increase width/height of the component |
| "full screen" | Component takes 100% of viewport |
| "put it on a second page" | Create a new view/tab |

The grid is flexible — components can overlap, resize, and reflow. Think of it like a tiling window manager controlled by voice.

---

## Persistence

Each user's layout is saved and restored on login:

```json
{
  "user_id": "sarah",
  "views": [
    {
      "name": "default",
      "components": [
        { "id": "emails-per-hour-chart", "spec": { ... } },
        { "id": "failed-workflows-table", "spec": { ... } },
        { "id": "daily-cost", "spec": { ... } }
      ]
    },
    {
      "name": "deep-dive",
      "components": [ ... ]
    }
  ]
}
```

Users can have multiple views ("show me my monitoring view" / "switch to the cost view").

---

## Intelligent Defaults for New Users

A blank canvas is intimidating. The system supports multiple on-ramps:

### Contextual presets via natural language

Users can request a complete UI in one command:

| Command | What the system generates |
|---------|--------------------------|
| "Create a default UI for a mid-sized accounting company" | Invoice volume chart, pending approvals queue, cost summary, recent errors, AP/AR status indicators |
| "Set me up for a legal team managing contracts" | Contract pipeline view, expiration timeline, signature status table, review queue |
| "I'm an IT admin, show me what I need" | System health, agent activity feed, cost trends, permission audit, error log |
| "Give me something simple to start" | Active workflow count, recent activity feed, conversation panel |
| "Make it look like what Sarah has" | Clone another user's layout (with permission) |

The system uses its knowledge of the industry/role to generate an appropriate starting layout — complete with relevant data sources, refresh rates, and behaviors. The user can then modify anything: "remove the AR status, add a chart of late payments by vendor."

### Role-based starting points

For users who don't give a specific command, the system offers a starting layout based on their role:

- **Operators**: active workflow count, recent failures, pending approvals
- **Designers**: workflow list, recent changes, test results
- **Admins**: system health, cost summary, user activity
- **Viewers**: summary dashboard, read-only activity feed

### Shared team layouts

Teams can publish layouts that new members inherit:

- "Save this layout as the Finance team default"
- New finance team members start with that layout
- Individual modifications don't affect the team default
- Team leads can update the shared layout: "add the new compliance widget to the team view"

### Layout marketplace (future)

Community-shared layouts for common use cases:
- "Invoice processing operations center"
- "Contract lifecycle management"
- "IT helpdesk monitoring"
- Import with one command, customize from there.

---

## Live Behavior and Interactivity

Components aren't just static displays. Users can add behaviors:

| User says | Behavior |
|-----------|----------|
| "Flash red when something fails" | Conditional styling triggered by events |
| "Play a sound when an approval is needed" | Audio alert on specific event |
| "Expand the details when I click a row" | Click interaction → detail panel |
| "Let me drag workflows between states" | Drag-and-drop interaction |
| "Auto-scroll the feed" | Scroll behavior on live feed |
| "Highlight anything over $100" | Conditional formatting on data |
| "Group by workflow type" | Data grouping/aggregation |
| "Show the last 24 hours" | Time range filter |

---

## The Conversation Panel

One component is always available (but can be minimized): the conversation panel. This is how users interact with the system:

- Create/modify workflows
- Ask questions ("why did that fail?")
- Modify the UI ("move that chart to the right")
- Give commands ("pause all invoice workflows")
- Get summaries ("what happened while I was away?")

The conversation panel is the primary interface. Everything else on screen is output the user has requested.

---

## Agent-Generated Code for UI Components

Following the same pattern as workflow steps: when a user requests a custom visualization or behavior that doesn't map to a standard component type, the UI agent can generate frontend code (TypeScript/Angular component) on the fly.

```
User: "Show me a heatmap of processing time by hour and day of week"

UI Agent: (no standard heatmap component exists)
  → Generates Angular component code
  → Validates and sandboxes
  → Renders in the user's view
```

This means the UI is not limited to a predefined set of widgets. If the user can describe it, the system can build it.

### Safety

Generated UI code is sandboxed:
- No access to other users' data
- No ability to modify system state (read-only unless explicitly granted)
- Runs in an iframe or web worker with restricted permissions
- Validated before rendering (no XSS, no external resource loading)

---

## Responsive and Multi-Device

The layout system adapts to screen size:
- Desktop: full grid, multiple components visible
- Tablet: stacked layout, fewer components visible at once
- Mobile: single-column, swipe between components
- The user can specify device-specific layouts: "on my phone, just show me the alert count and pending approvals"

---

## What This Replaces

| Traditional Feature | Generative UI Equivalent |
|--------------------|--------------------------|
| Dashboard page | User-composed view with requested components |
| Settings page | "Set X to Y" in conversation |
| Workflow list page | "Show me my workflows" → table component |
| Monitoring page | "Show me system health" → status indicators + charts |
| Admin panel | "Show me user activity and costs" → tables + charts |
| Help/docs | "How do I..." in conversation → contextual answer |
| Navigation menu | "Show me..." / "Switch to..." / named views |

There are no pages. There are no menus. There is a canvas and a conversation.
