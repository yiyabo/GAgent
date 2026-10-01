import { describe, expect, it } from 'vitest';
import { findCorrelatedAssistant } from './turnCorrelation';

describe('current turn correlation', () => {
  it('rejects an assistant from another run even inside a matching user interval', () => {
    expect(findCorrelatedAssistant([
      { role: 'user', metadata: { client_message_id: 'turn-A' } },
      { role: 'assistant', content: 'Unrelated', metadata: { deep_think_job_id: 'run-B' } },
    ], { runId: 'run-A', clientMessageId: 'turn-A' })).toBeNull();
  });

  it('stops client-message correlation at the next user turn', () => {
    expect(findCorrelatedAssistant([
      { role: 'user', metadata: { client_message_id: 'turn-A' } },
      { role: 'user', metadata: { client_message_id: 'turn-B' } },
      { role: 'assistant', content: 'Answer B' },
    ], { clientMessageId: 'turn-A' })).toBeNull();
  });

  it('requires positive turn evidence and terminal nonempty content', () => {
    expect(findCorrelatedAssistant([{ role: 'assistant', content: 'A long recent response' }], {})).toBeNull();
    expect(findCorrelatedAssistant([{ role: 'assistant', content: 'Partial', metadata: { chat_run_id: 'run-A', status: 'running' } }], { runId: 'run-A' })).toBeNull();
    expect(findCorrelatedAssistant([{ role: 'assistant', content: 'Partial', metadata: { chat_run_id: 'run-A', status: 'streaming' } }], { runId: 'run-A' })).toBeNull();
    expect(findCorrelatedAssistant([{ role: 'assistant', content: ' ', metadata: { chat_run_id: 'run-A', status: 'completed' } }], { runId: 'run-A' })).toBeNull();
  });
});
