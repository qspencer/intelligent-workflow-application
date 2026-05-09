# AWS Bedrock setup — clearing the access gates

This is the friction list an operator encounters between "AWS credentials exist" and "the platform can call a Claude model on Bedrock." Hit each gate once per account; production accounts have to repeat the steps.

The smoke script at `backend/tools/smoke_live.py` exercises the full path. Run it after each step to know whether the gate is cleared.

## Gate 1 — Model access enabled

**Symptom**

```
ResourceNotFoundException: An error occurred (ResourceNotFoundException)
when calling the Converse operation: Access denied. This Model is marked
by provider as Legacy and you have not been actively using the model in
the last 30 days. Please upgrade to an active model on Amazon Bedrock
```

…or, on a never-used account:

```
AccessDeniedException: Your account is not authorized to invoke this
API operation
```

**Why** — Bedrock requires an explicit per-account, per-model opt-in. Some Anthropic models also auto-deactivate after 30 days of non-use ("legacy") and need to be re-requested.

**Fix**

1. AWS Console → **Bedrock** → **Model access** (left sidebar).
2. Click **Modify model access**.
3. Tick the Anthropic models you intend to use. For this platform: at least one Claude 4.x family model (Haiku 4.5 is the cheapest active option as of the date this was written; check `aws bedrock list-foundation-models --by-provider Anthropic` for what's currently active in your region).
4. Submit. Most models grant within seconds. Some take minutes.

**Verify** — `aws bedrock list-foundation-models --region us-east-1 --by-provider Anthropic --query 'modelSummaries[?modelLifecycle.status==\`ACTIVE\`].modelId' --output text` should return at least one Claude 4 model.

## Gate 2 — Inference profile required for Claude 4.x

**Symptom**

```
ValidationException: Invocation of model ID anthropic.claude-sonnet-4-6
with on-demand throughput isn't supported. Retry your request with the
ID or ARN of an inference profile that contains this model.
```

**Why** — Anthropic's Claude 4 family on Bedrock isn't directly invokable; it must go through an *inference profile* (AWS's cross-region routing primitive). The error is unambiguous about this; the fix is just to use the profile id.

**Fix** — pass the profile id (or ARN) as `modelId`. Profile ids look like `us.anthropic.claude-haiku-4-5-20251001-v1:0` or `global.anthropic.claude-sonnet-4-6` — the prefix (`us.`, `eu.`, `apac.`, `global.`) names the routing region(s).

Discover what your account has:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?status==`ACTIVE`].{name:inferenceProfileName, id:inferenceProfileId}' \
  --output table
```

In the codebase, set the model on the workflow definition's agentic step:

```json
{
  "type": "agentic",
  "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  ...
}
```

The Bedrock client passes the value through verbatim as `modelId`; no changes to the client are needed.

## Gate 3 — Anthropic "use case details" form

**Symptom**

```
ResourceNotFoundException: Model use case details have not been
submitted for this account. Fill out the Anthropic use case details
form before using the model. If you have already filled out the form,
try again in 15 minutes.
```

**Why** — Anthropic requires a one-time use-case attestation per AWS account before its models will respond on Bedrock, separately from the model-access opt-in. Auth is fine, IAM is fine, the inference profile is correct — Bedrock still refuses until the form is on file.

**Fix**

1. AWS Console → **Bedrock** → **Bedrock configurations** (left sidebar).
2. Find the **"Use case details"** link or button (the exact label has shifted between AWS UI revisions; Anthropic models surface a banner prompting it).
3. Submit the form: company name, intended use, expected volume, etc. Free-form, no commitment.
4. Wait ~15 minutes. The error message is honest about the propagation delay.

**Verify** — re-run `uv run python tools/smoke_live.py` from `backend/`. All three steps (direct converse / Agent.run / WorkflowEngine) should print successful output and small token / cost numbers.

## Gate 4 — Account-level Bedrock service quotas

**Symptom (much later, under load)**

```
ThrottlingException: Too many requests, please wait before trying again
```

**Why** — fresh AWS accounts get conservative TPM (tokens-per-minute) and RPM (requests-per-minute) limits per model. Production traffic blows through them quickly.

**Fix** — AWS Console → **Service Quotas** → **AWS services** → **Amazon Bedrock**. Filter for the model you care about and request a quota increase. Approval takes hours to days depending on volume requested. There's no API for this; the form is mandatory.

**Verify** — `aws service-quotas list-service-quotas --service-code bedrock --region us-east-1` shows current values; bump the ones for your model.

## What changes between dev and prod

| Concern | Dev account | Production account |
|---|---|---|
| Model access | Click through whatever is needed | Maintain a documented allowlist; review on schedule |
| Use case form | One person, one time | Re-submit when account ownership / use case changes (uncommon) |
| Inference profile | Pick the cheapest active one | Pin a specific profile in the workflow definition; document why |
| Quotas | Defaults usually fine | Request increases *before* launch; observe TPM/RPM dashboards |
| Region | Wherever the cheapest active models are | Match your data residency policy; mind that profile names embed the region |
| Cost guardrails | `WORKFLOW_PLATFORM_PRICING` defaults | Set workflow-level `policies.max_total_tokens` + `budget_action` to match your billing tolerance |

## Reproducing what we already learned

Each gate above corresponds to an actual error our smoke script surfaced on the development account on 2026-05-09:

1. Gate 1: Haiku 3 marked legacy after 30 days unused.
2. Gate 2: `claude-sonnet-4-6` rejected on-demand; only the inference profile ARN works.
3. Gate 3: Use case details form never submitted; even Haiku 4.5 via inference profile failed.

`backend/tools/smoke_live.py` is the canonical "did I actually clear the gate?" check. If you re-run it after each step you'll know exactly which gate remains.

## What this doc deliberately doesn't cover

- **Bedrock provisioned throughput**: not used by this platform. We rely on on-demand + inference profiles.
- **Cross-account Bedrock**: not supported by the current IAM in `infra/iam.tf` (which restricts to Anthropic foundation-model ARNs in the local account).
- **Custom model deployment**: out of scope.
