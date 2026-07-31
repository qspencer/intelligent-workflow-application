# Sample Run Artifact — codified-route email triage (sanitized)

Instance `1d48155a…` of `email-triage-apply`, captured from the
live single-operator deployment 2026-07-31. Mail content (subjects,
bodies, summaries, display names) is redacted; email local parts are
redacted keeping domains; structure, decisions, evidence, costs, and
the complete audit sequence are intact.

This run took the **codified-sender fast path**: the DKIM/DMARC-
authenticated sender matched an evidence-promoted rule, so the
category came from the rule (zero category-classification tokens),
the attention-only classifier still read the message, and the
minimized apply agent made exactly one mutation.

## Instance
```json
{
 "id": "1d48155a-cb40-4c35-bcd6-08b1c6cb8fd0",
 "workflow_id": "email-triage-apply",
 "state": "completed",
 "trigger_payload": {
  "cc": [],
  "to": [
   {
    "name": null,
    "address": "[redacted]@gmail.com"
   }
  ],
  "bcc": [],
  "labels": [
   "CATEGORY_PROMOTIONS",
   "UNREAD",
   "Label_2133737228660024788"
  ],
  "headers": {},
  "subject": "[REDACTED \u2014 mail content]",
  "provider": "gmail",
  "auth_pass": true,
  "body_html": null,
  "body_text": "[REDACTED \u2014 mail content]",
  "thread_id": "19fb9c7fc9ba1f62",
  "message_id": "19fb9c7fc9ba1f62",
  "attachments": [],
  "in_reply_to": null,
  "received_at": "2026-07-31T20:05:18Z",
  "from_address": {
   "name": "[REDACTED \u2014 mail content]",
   "address": "[redacted]@mail.simplyrecipes.com"
  },
  "reply_status": "not_replied"
 },
 "context": {
  "steps": {
   "apply": {
    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "usage": {
     "iterations": 2,
     "tool_calls": 1,
     "input_tokens": 1987,
     "total_tokens": 2095,
     "output_tokens": 108
    },
    "recall": null,
    "cost_usd": 0.002527,
    "tool_calls": [
     {
      "name": "[REDACTED \u2014 mail content]",
      "input": {
       "labels": [
        "wf/newsletter"
       ],
       "message_id": "19fb9c7fc9ba1f62"
      },
      "result": {
       "error": null,
       "content": {
        "message_id": "19fb9c7fc9ba1f62",
        "labels_applied": [
         "wf/newsletter"
        ]
       }
      }
     }
    ],
    "memory_hash": null,
    "output_text": "Applied labels: wf/newsletter",
    "stop_reason": "end_turn"
   },
   "record": {
    "category": "newsletter",
    "parse_ok": true,
    "attention": [],
    "apply_labels": [
     "wf/newsletter"
    ],
    "decision_note": "[REDACTED \u2014 mail content]",
    "rule_evidence": {
     "distinct_messages": 6,
     "current_schema_messages": 6
    },
    "category_valid": true,
    "attention_valid": true,
    "decision_source": "codified_sender_rule",
    "model_confidence": null,
    "apply_label_count": 1,
    "triage_schema_version": 2
   },
   "precheck": {
    "route": "codified",
    "listed": true,
    "sampled": false,
    "authenticated": true,
    "rule_category": "newsletter",
    "rule_evidence": {
     "distinct_messages": 6,
     "current_schema_messages": 6
    },
    "rule_compatible": true
   },
   "classify_attention": {
    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "usage": {
     "iterations": 1,
     "tool_calls": 0,
     "input_tokens": 6816,
     "total_tokens": 6856,
     "output_tokens": 40
    
```

## Steps

### `precheck` — completed
```json
{
 "route": "codified",
 "listed": true,
 "sampled": false,
 "authenticated": true,
 "rule_category": "newsletter",
 "rule_evidence": {
  "distinct_messages": 6,
  "current_schema_messages": 6
 },
 "rule_compatible": true
}
```
### `triage` — skipped
```json
null
```
### `classify_attention` — completed
```json
{
 "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
 "usage": {
  "iterations": 1,
  "tool_calls": 0,
  "input_tokens": 6816,
  "total_tokens": 6856,
  "output_tokens": 40
 },
 "recall": {
  "edges": 40,
  "query": "[redacted]@mail.simplyrecipes.com",
  "episodes": 12,
  "context_hash": "sha256:7a6f7fc05d7cffc8"
 },
 "cost_usd": 0.007016,
 "tool_calls": [],
 "memory_hash": "sha256:3bef3b8b35614a77",
 "output_text": "```json\n{\"attention\": [], \"decision_note\": \"Routine recipe newsletter from a validated, subscribed sender; no time-sensitive action or reply required.\"}\n```",
 "stop_reason": "end_turn"
}
```
### `record` — completed
```json
{
 "category": "newsletter",
 "parse_ok": true,
 "attention": [],
 "apply_labels": [
  "wf/newsletter"
 ],
 "decision_note": "[REDACTED \u2014 mail content]",
 "rule_evidence": {
  "distinct_messages": 6,
  "current_schema_messages": 6
 },
 "category_valid": true,
 "attention_valid": true,
 "decision_source": "codified_sender_rule",
 "model_confidence": null,
 "apply_label_count": 1,
 "triage_schema_version": 2
}
```
### `apply` — completed
```json
{
 "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
 "usage": {
  "iterations": 2,
  "tool_calls": 1,
  "input_tokens": 1987,
  "total_tokens": 2095,
  "output_tokens": 108
 },
 "recall": null,
 "cost_usd": 0.002527,
 "tool_calls": [
  {
   "name": "[REDACTED \u2014 mail content]",
   "input": {
    "labels": [
     "wf/newsletter"
    ],
    "message_id": "19fb9c7fc9ba1f62"
   },
   "result": {
    "error": null,
    "content": {
     "message_id": "19fb9c7fc9ba1f62",
     "labels_applied": [
      "wf/newsletter"
     ]
    }
   }
  }
 ],
 "memory_hash": null,
 "output_text": "Applied labels: wf/newsletter",
 "stop_reason": "end_turn"
}
```

## Audit trail (complete, in order)

| time | actor | action | step | detail (scrubbed) |
|---|---|---|---|---|
| 20:05:41 | engine:workflow_engine | workflow_started |  | `{"trigger": {"cc": [], "to": [{"name": null, "address": "[redacted]@gmail.com"}], "bcc": [], "labels": ["CATEGORY_PROMOTIONS", "UNREAD", "Label_2133737228660024788"], "headers": {}, "subject": "[REDACTED \u2014 mail cont…` |
| 20:05:41 | engine:step:precheck | step_started | precheck | `{"type": "deterministic", "attempt": 1}` |
| 20:05:41 | engine:step:precheck | step_completed | precheck | `{"output": {"route": "codified", "listed": true, "sampled": false, "authenticated": true, "rule_category": "newsletter", "rule_evidence": {"distinct_messages": 6, "current_schema_messages": 6}, "rule_compatible": true}, …` |
| 20:05:41 | engine:step:triage | step_skipped | triage | `{}` |
| 20:05:41 | engine:step:classify_attention | step_started | classify_attention | `{"type": "agentic", "attempt": 1}` |
| 20:06:05 | engine:learned_memory | memory_recalled | classify_attention | `{"edges": 40, "query": "[redacted]@mail.simplyrecipes.com", "user_id": "org:default:user:[redacted]@gmail.com", "episodes": 12, "injected": true, "context_hash": "sha256:7a6f7fc05d7cffc8", "token_budget": 600, "uses_reco…` |
| 20:06:06 | engine:step:classify_attention | step_completed | classify_attention | `{"output": {"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "usage": {"iterations": 1, "tool_calls": 0, "input_tokens": 6816, "total_tokens": 6856, "output_tokens": 40}, "recall": {"edges": 40, "query": "[redacte…` |
| 20:06:06 | engine:step:record | step_started | record | `{"type": "deterministic", "attempt": 1}` |
| 20:06:06 | engine:step:record | step_completed | record | `{"output": {"category": "newsletter", "parse_ok": true, "attention": [], "apply_labels": ["wf/newsletter"], "decision_note": "[REDACTED \u2014 mail content]", "rule_evidence": {"distinct_messages": 6, "current_schema_mes…` |
| 20:06:06 | engine:step:apply | step_started | apply | `{"type": "agentic", "attempt": 1}` |
| 20:06:10 | agent:steps/email-triage-apply/apply | tool_call | apply | `{"name": "[REDACTED \u2014 mail content]", "input": {"labels": ["wf/newsletter"], "message_id": "19fb9c7fc9ba1f62"}, "result": {"error": null, "content": {"message_id": "19fb9c7fc9ba1f62", "labels_applied": ["wf/newslett…` |
| 20:06:10 | engine:step:apply | step_completed | apply | `{"output": {"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "usage": {"iterations": 2, "tool_calls": 1, "input_tokens": 1987, "total_tokens": 2095, "output_tokens": 108}, "recall": null, "cost_usd": 0.002527, "to…` |
| 20:06:12 | engine:learned_memory | memory_observed |  | `{"facts": 2, "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "author": "third_party", "user_id": "org:default:user:[redacted]@gmail.com", "cost_usd": 0.004493, "text_hash": "sha256:c745eadcdba19b5c", "event_type"…` |
| 20:06:15 | engine:learned_memory | memory_observed |  | `{"facts": 2, "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "author": "system", "user_id": "org:default:user:[redacted]@gmail.com", "cost_usd": 0.001911, "text_hash": "sha256:c3e783a1c04c11d9", "event_type": "tr…` |
| 20:06:15 | engine:workflow_engine | workflow_completed |  | `{"steps": ["precheck", "classify_attention", "record", "apply"]}` |
