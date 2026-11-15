---
name: plan-mode-guidance
description: Suggest explicit Plan mode for complex multi-stage research and execution. Use plan_operation.offer with the original request, stop for user consent, and never enable planning or execute silently.
---

# Plan Mode Guidance

Use this Skill when the current request would benefit from read-only research and a reviewable Plan before side effects.

## When to offer

Offer only when all of these are true:

- The task has multiple research/execution stages, will write result artifacts, or will submit bioinformatics jobs.
- No Plan is currently bound and explicit Plan mode is not already enabled.
- The user has not explicitly requested a direct answer, declined planning, or asked not to create a Plan.
- The request is not a simple question, one-tool lookup, status check, explanation, or casual follow-up.

If the user already asked for a structured Plan, use the normal Plan creation path directly instead of offering. If the current session has declined an offer, continue normally without another offer.

## How to offer

Call `plan_operation` with:

```json
{
  "operation": "offer",
  "reason": "One concise reason in the user's language",
  "query": "The user's original request, verbatim"
}
```

After a pending offer is returned, stop immediately. Ask the user to choose **Enter Plan mode and create a Plan** or **Continue directly**. Do not call another tool, create a Plan, write result artifacts, or start execution while the offer is pending.

If the result contains `suppressed`, continue with the current request without showing another offer.

## Consent boundary

The model must never set `plan_mode`, interpret its own suggestion as consent, or treat an offer as permission to execute. Acceptance arrives only from the client as a later request with `plan_offer_accepted=true` and explicit Plan mode. That request must create a structured Plan through `plan_operation.create`.

Plan creation and Plan execution are separate approvals. Even after the user accepts the offer, do not execute until the user separately approves the created Plan.
