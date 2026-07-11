"""Authoring catalog (C7.2): the trigger types, deterministic step functions, and
agent tools available when building a workflow on the canvas.

The canvas turns this into a searchable palette so authors pick from named,
described building blocks instead of typing raw function / tool / trigger
strings. Functions + tools reflect what the running engine actually has wired
(so the picker can't offer something that would fail at runtime); the trigger
set is fixed by the orchestrator's `_make_trigger` branches.
"""

from __future__ import annotations

import inspect

from pydantic import BaseModel

from workflow_platform.engine import FunctionRegistry, ToolCatalog


class CatalogField(BaseModel):
    name: str
    required: bool = False
    description: str = ""


class TriggerCatalogItem(BaseModel):
    type: str
    label: str
    description: str
    config_fields: list[CatalogField] = []


class FunctionCatalogItem(BaseModel):
    name: str
    description: str


class ToolCatalogItem(BaseModel):
    name: str
    description: str
    category: str


class WorkflowCatalog(BaseModel):
    triggers: list[TriggerCatalogItem]
    functions: list[FunctionCatalogItem]
    tools: list[ToolCatalogItem]


# Fixed trigger set — mirrors orchestrator._make_trigger. Config fields name the
# keys that branch reads, so the canvas can prompt for them.
TRIGGERS: list[TriggerCatalogItem] = [
    TriggerCatalogItem(
        type="manual",
        label="Manual",
        description="Run on demand — the Run button, the API, or tools/fire.py.",
    ),
    TriggerCatalogItem(
        type="filesystem",
        label="File drop",
        description="Watch a folder and fire when a matching file appears.",
        config_fields=[
            CatalogField(name="path", required=True, description="Folder to watch."),
            CatalogField(name="pattern", description="Glob to match (default '*')."),
            CatalogField(name="recursive", description="Watch subfolders too (true/false)."),
        ],
    ),
    TriggerCatalogItem(
        type="schedule",
        label="Schedule",
        description="Fire on a cron schedule or a fixed interval.",
        config_fields=[
            CatalogField(name="cron", description="Cron expression, e.g. '0 9 * * 1'."),
            CatalogField(name="interval_seconds", description="Fixed interval instead of cron."),
            CatalogField(name="timezone", description="IANA tz for the cron (default UTC)."),
        ],
    ),
    TriggerCatalogItem(
        type="webhook",
        label="Webhook",
        description="Fire on an inbound HTTP POST to /api/triggers/webhook/{id}.",
        config_fields=[
            CatalogField(name="trigger_id", description="Webhook id (defaults to workflow id)."),
        ],
    ),
    TriggerCatalogItem(
        type="email",
        label="Email",
        description="Watch a mailbox and fire once per new message. "
        "(`gmail_poll` is the accepted legacy alias for provider=gmail.)",
        config_fields=[
            CatalogField(
                name="provider",
                description="Email provider (default 'gmail'; others as they land).",
            ),
            CatalogField(
                name="account", required=True, description="Mailbox address from .secrets."
            ),
            CatalogField(name="label", description="Mailbox label to poll (default INBOX)."),
            CatalogField(name="poll_interval_seconds", description="Seconds between polls."),
            CatalogField(name="max_messages", description="Max messages per poll."),
            CatalogField(
                name="query",
                description="Extra provider-native search clause, e.g. Gmail's "
                "'has:attachment filename:zip'.",
            ),
            CatalogField(
                name="slim_payload",
                description="Drop body_html + raw headers from the payload "
                "(recommended for triage agents; saves ~40k tokens/message).",
            ),
            CatalogField(
                name="download_dir",
                description="Spool dir: attachments are downloaded here and their paths "
                "added to the payload as attachment_paths.",
            ),
        ],
    ),
]

# Tool category by exact name; browser_* falls back via prefix below.
_TOOL_CATEGORY: dict[str, str] = {
    "pdf_extract": "document",
    "image_ocr": "document",
    "file_read": "filesystem",
    "file_write": "filesystem",
    "connector_send": "connector",
    "connector_query": "connector",
    "email_send": "email",
    "email_label_apply": "email",
    "request_human_review": "human",
}


def _tool_category(name: str) -> str:
    if name in _TOOL_CATEGORY:
        return _TOOL_CATEGORY[name]
    if name.startswith("browser_"):
        return "browser"
    return "core"


def _first_doc_line(obj: object) -> str:
    doc = inspect.getdoc(obj)
    if not doc:
        return ""
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def build_catalog(functions: FunctionRegistry, tools: ToolCatalog) -> WorkflowCatalog:
    """Build the authoring catalog from the engine's live registries."""
    func_items = [
        FunctionCatalogItem(name=name, description=_first_doc_line(functions.get(name)))
        for name in sorted(functions.names())
    ]
    tool_items = []
    for name in sorted(tools.names()):
        tool = tools.get(name)
        tool_items.append(
            ToolCatalogItem(
                name=name,
                description=getattr(tool, "description", "") or "",
                category=_tool_category(name),
            )
        )
    return WorkflowCatalog(triggers=TRIGGERS, functions=func_items, tools=tool_items)
