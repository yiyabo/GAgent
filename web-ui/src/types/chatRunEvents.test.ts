import { describe, expect, it } from 'vitest';
import canonical from './chatRunEvents.canonical.json';
import { CHAT_RUN_EVENT_TYPES, validateChatRunEvent } from './chatRunEvents';

describe('chatRunEvents contract', () => {
    it('canonical examples from the backend registry all validate', () => {
        for (const [type, payload] of Object.entries(canonical)) {
            expect(validateChatRunEvent(payload), `canonical ${type}`).toBeNull();
        }
    });

    it('canonical set covers every registered event type', () => {
        expect(Object.keys(canonical).sort()).toEqual([...CHAT_RUN_EVENT_TYPES].sort());
    });

    it('rejects unknown types and malformed core fields', () => {
        expect(validateChatRunEvent({ type: 'nope' })).toMatch(/unknown event type/);
        expect(validateChatRunEvent({ type: 'thinking_delta', iteration: 'x', delta: 'a' })).toMatch(/iteration/);
        expect(validateChatRunEvent({ type: 'delta' })).toMatch(/content/);
        expect(validateChatRunEvent('not-an-object')).toMatch(/not an object/);
        expect(validateChatRunEvent({})).toMatch(/missing event type/);
    });

    it('allows extra keys to evolve freely', () => {
        expect(validateChatRunEvent({ type: 'delta', content: 'x', future_field: 1 })).toBeNull();
    });
});
