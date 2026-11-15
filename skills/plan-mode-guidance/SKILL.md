---
name: plan-mode-guidance
description: Suggest explicit Plan mode for complex multi-stage research and execution, and ask structured clarifying questions while drafting a Plan. Use plan_operation.offer for consent and plan_operation.ask_user for blocking decisions; never enable planning or execute silently.
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

## Clarifying questions in Plan mode

While explicit Plan mode is active, investigate first, then ask only what you cannot look up.

1. **Look before asking.** Use read-only tools (literature search, PhageScope data, uploaded files, the conversation) to settle facts. Never ask the user something a tool can answer.
2. **Ask only blocking decisions.** A question is worth asking only if different answers would produce a materially different Plan: target host or strain range, data source (PhageScope, NCBI, the user's own data), validation depth (computational only or planned wet-lab work), deliverable form (review, candidate list, executable pipeline), or time/compute budget.
3. **Call `plan_operation` with `operation="ask_user"`**:

```json
{
  "operation": "ask_user",
  "context": "One sentence on what you found and why these choices matter",
  "questions": [
    {
      "id": "host_range",
      "header": "宿主范围",
      "question": "计划聚焦哪类宿主菌？",
      "options": [
        {"label": "仅金黄色葡萄球菌", "description": "范围最窄，可最快给出候选"},
        {"label": "葡萄球菌属", "description": "覆盖更多菌株，检索与验证工作量更大"}
      ]
    }
  ]
}
```

   - 1-3 questions (prefer 1), each with 2-4 mutually exclusive options; put your recommended option first. The client adds "Other" and "Need more context" automatically, so do not include them.
   - Write questions and options in the user's language. Each option description states its impact on the Plan.
4. **Stop after asking.** The turn ends with a question card. Do not create, edit or execute a Plan until the answer arrives as the next message.
5. **Never ask** whether the Plan is ready, whether to proceed, or for approval. Plan approval is a separate step.
6. **At most two rounds per Plan.** When the tool reports `clarification_limit_reached`, continue with the recommended options.
7. **Record assumptions.** When the user chose "continue with recommendations", or a decision is still unconfirmed, pass those choices in `plan_operation create` as `assumptions=["..."]`. They appear in the Plan panel so the user can review them.
8. If the user asks for more context, explain concretely what each option changes, then ask again with clearer options; this does not consume a round.
