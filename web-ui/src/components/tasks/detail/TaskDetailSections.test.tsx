import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ExecutionResult } from './TaskDetailSections';

describe('ExecutionResult', () => {
  it('renders verification state and re-verify action', () => {
    const onReverify = vi.fn();

    render(
      <ExecutionResult
        resultLoading={false}
        taskResult={{
          task_id: 22,
          status: 'failed',
          content: 'candidate files downloaded',
          metadata: {
            verification: {
              status: 'failed',
              checks_total: 2,
              checks_passed: 1,
              blocking: true,
              generated: false,
              failures: [
                {
                  type: 'pdb_residue_present',
                  path: '/tmp/1RH5_SEC.pdb',
                  message: 'SEC residue not found',
                },
              ],
              evidence: {
                artifact_paths: ['/tmp/1RH5_SEC.pdb'],
              },
            },
          },
        }}
        cachedResult={undefined}
        canVerify
        onReverify={onReverify}
      />
    );

    expect(screen.getByText('Verification failed')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Re-verify' }));
    expect(onReverify).toHaveBeenCalledTimes(1);
  });
});
