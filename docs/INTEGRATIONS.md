# Intelligent Workflow Platform — Integrations Strategy

## Principle

Integrations are bidirectional: any connected system can be a **trigger** (start a workflow) and a **destination** (receive output from a workflow). The connector architecture is the same in both directions — a single "SharePoint connector" handles both "new file in SharePoint triggers a workflow" and "workflow saves output to SharePoint."

---

## Connector Architecture

```
┌─────────────────────────────────────────────┐
│              Connector Interface             │
├─────────────────────────────────────────────┤
│  trigger_listen()  → emits events           │
│  trigger_poll()    → checks for new items   │
│  send(payload)     → writes to destination  │
│  query(params)     → reads from system      │
│  authenticate()    → handles OAuth/API keys │
│  health_check()    → verifies connectivity  │
└─────────────────────────────────────────────┘
```

Each connector is a plugin. Adding a new integration means implementing this interface for that system.

---

## Priority Tiers

### Tier 1 — Launch (target audience daily drivers)

| System | Trigger Examples | Output Examples |
|--------|-----------------|-----------------|
| **Microsoft 365 / SharePoint** | New file in library, new list item, file modified | Save document, update list item, create folder |
| **Microsoft Outlook / Exchange** | Email received (with attachment), calendar event | Send email, create calendar event, reply |
| **Microsoft Teams** | Message in channel, adaptive card response | Post message, send adaptive card, create channel |
| **Google Drive** | New file, file modified, shared with me | Upload file, create doc, update sheet |
| **Gmail** | Email received, label applied | Send email, apply label, create draft |
| **Slack** | Message in channel, slash command, reaction added | Post message, upload file, update message |
| **Amazon S3** | Object created, object modified (via EventBridge) | Put object, generate presigned URL |
| **REST/Webhook (generic)** | Incoming HTTP POST | Outgoing HTTP request (any method) |

### Tier 2 — Fast follow (enterprise systems)

| System | Trigger Examples | Output Examples |
|--------|-----------------|-----------------|
| **Salesforce** | Record created/updated, case opened | Create/update record, add note, change status |
| **ServiceNow** | Incident created, change request approved | Create incident, update ticket, add comment |
| **Jira** | Issue created/transitioned, comment added | Create issue, transition, add comment |
| **SAP** | Document posted, PO created | Create document, update master data |
| **Oracle ERP** | Invoice received, approval completed | Post journal entry, update vendor |
| **Microsoft Dynamics 365** | Record created, workflow triggered | Create/update entity, trigger action |
| **DocuSign / Adobe Sign** | Document signed, envelope completed | Send for signature, download signed doc |

### Tier 3 — Expansion (productivity & data)

| System | Trigger Examples | Output Examples |
|--------|-----------------|-----------------|
| **Microsoft Excel Online** | Row added, cell changed | Update cells, add row, create workbook |
| **Google Sheets** | Row added, cell changed | Update cells, add row, create sheet |
| **Notion** | Page created, database item added | Create page, update database, add comment |
| **Confluence** | Page created/updated | Create/update page, add comment |
| **Box** | File uploaded, shared | Upload file, create folder, add metadata |
| **Dropbox** | File added, folder changed | Upload file, create shared link |
| **Power BI** | Dataset refreshed | Push data, trigger refresh |
| **Snowflake / BigQuery** | Query result available | Insert rows, run query, export results |
| **Twilio** | SMS received, call completed | Send SMS, make call |
| **PagerDuty** | Incident triggered | Create incident, acknowledge, resolve |

---

## Authentication Per Connector

| Auth Method | Systems |
|-------------|---------|
| OAuth 2.0 | Microsoft 365, Google, Salesforce, Slack, Jira, DocuSign |
| API Key / Token | Slack (bot token), ServiceNow, generic webhook |
| AWS IAM | S3, Bedrock, other AWS services |
| Basic Auth | Legacy systems, some on-prem |
| Service Account / JWT | Google (server-to-server), SAP |
| SAML assertion | Some enterprise SSO-gated APIs |

Credentials stored in secrets management (AWS Secrets Manager for SaaS, customer's vault for self-hosted). Never in workflow definitions or memory files.

---

## Connector Configuration Model

```json
{
  "id": "sharepoint-finance-team",
  "type": "microsoft_sharepoint",
  "name": "Finance Team SharePoint",
  "auth": {
    "method": "oauth2",
    "tenant_id": "...",
    "client_id": "...",
    "secret_ref": "secrets/sharepoint-finance"
  },
  "config": {
    "site_url": "https://contoso.sharepoint.com/sites/finance",
    "default_library": "Shared Documents"
  },
  "capabilities": {
    "can_trigger": true,
    "can_output": true,
    "can_query": true
  }
}
```

---

## How Connectors Appear in the Platform

### As Triggers

In a workflow definition:
```json
{
  "trigger": {
    "type": "microsoft_sharepoint",
    "connector_id": "sharepoint-finance-team",
    "event": "file_created",
    "config": { "library": "Invoices", "pattern": "*.pdf" }
  }
}
```

In natural language authoring:
> "When a new PDF appears in the Finance team's SharePoint Invoices folder..."

### As Tools (available to agents)

Connectors register as tools that agents can use:
- `sharepoint_upload(connector_id, library, filename, content)`
- `sharepoint_list_files(connector_id, library, filter)`
- `slack_post_message(connector_id, channel, text)`
- `jira_create_issue(connector_id, project, summary, description)`

Agent capability sets control which connectors an agent can use.

### As Output Steps (deterministic)

In a workflow step:
```json
{
  "id": "save_to_sharepoint",
  "type": "deterministic",
  "function": "connector_send",
  "config": {
    "connector_id": "sharepoint-finance-team",
    "action": "upload_file",
    "params": { "library": "Processed", "filename": "$context.filename" }
  }
}
```

---

## Implementation Approach

### Phase 1: Connector framework + generic webhook + S3

Build the plugin interface, auth management, and credential storage. Ship with webhook (most flexible, works with anything) and S3 (already in AWS ecosystem).

### Phase 2: Microsoft 365 suite

SharePoint, Outlook, Teams. Covers the largest enterprise user base. Uses Microsoft Graph API for all three — one OAuth flow, multiple capabilities.

### Phase 3: Google Workspace + Slack

Google Drive, Gmail, Sheets. Slack. Covers the rest of the collaboration market.

### Phase 4: Enterprise systems

Salesforce, ServiceNow, Jira. Prioritize based on customer demand.

### Phase 5: Long tail

Community-contributed connectors via the plugin interface. Marketplace potential.

---

## Connector Development Kit

To enable rapid connector development (internal and eventually third-party):

- Connector template/scaffold generator
- Standard test harness: mock the external API, verify trigger/send/query behavior
- Auth flow helpers: OAuth 2.0 dance, token refresh, secret storage
- Documentation template: what the connector does, required permissions, configuration fields
- Versioning: connectors are versioned independently of the platform
