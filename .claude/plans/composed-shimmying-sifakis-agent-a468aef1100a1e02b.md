# Fix Disordered Chat Messages and Thinking Process Steps

## Investigation Summary

After examining the codebase, here is what was found:

### Backend emit-before-save is NOT an issue
Contrary to the problem statement, both the deep-think path (agent.py:2702 save, 2748 yield) and the simple-chat path (agent.py:2885 save, 2913 yield) already save BEFORE yielding the final event. No backend timing fix is needed.

### Backend history ordering is correct but fragile
`session_helpers.py:1098-1114` uses `ORDER BY id DESC` then `rows.reverse()`. Since `id` is an auto-increment integer assigned at INSERT time, this is chronologically correct. SQLite serializes writes via its locking, so auto-increment IDs always reflect insertion order. No change needed here.

### The real bugs are all frontend

---

## Fix 1: Thinking step insertion order (HIGH IMPACT, SIMPLE)

**File:** `web-ui/src/store/slices/message/streamHandlers.ts`
**Lines:** 439-448

**Problem:** When a new thinking step arrives for an iteration that doesn't already exist, it is `push()`ed to the end of the `updatedSteps` array. If steps arrive out of order (e.g., iteration 3 arrives before iteration 2), they appear in arrival order rather than iteration order.

**Fix:** Replace `push()` with sorted insertion using `splice()` at the correct position.

---

## Fix 2: Defensive sort in _hydrateThinkingProcess (MEDIUM IMPACT, SIMPLE)

**File:** `web-ui/src/store/slices/message/index.ts`
**Lines:** 190-217

**Problem:** When loading historical messages, thinking steps from the database are hydrated in whatever order they were stored.

**Fix:** Add `.sort((a, b) => a.iteration - b.iteration)` after the `.map()` chain.

---

## Fix 3: Defensive sort in ThinkingProcess rendering (LOW IMPACT, SAFETY NET)

**File:** `web-ui/src/components/chat/ThinkingProcess.tsx`
**Line:** 201-211

**Problem:** `getMainSteps` filters steps but does not sort them.

**Fix:** Sort a shallow copy of steps by iteration before filtering in `getMainSteps`.

---

## Fix 4: Sort messages after deduplication in history load (MEDIUM IMPACT, SIMPLE)

**File:** `web-ui/src/store/slices/message/index.ts`
**Lines:** 391-397

**Problem:** After merge+dedup, messages may not be in chronological order.

**Fix:** Add `.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())` after the dedup filter.

---

## Implementation Order

1. Fix 1 - streaming step insertion
2. Fix 3 - rendering safety net
3. Fix 2 - history hydration sort
4. Fix 4 - message-level sort

## What NOT to change

- Backend agent.py save/emit ordering (already correct)
- Backend session_helpers.py ORDER BY (correct for SQLite)
- SQLite schema (no migration needed)
