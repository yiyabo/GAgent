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
            execution_status: 'completed',
            failure_kind: 'contract_mismatch',
            artifact_verification: {
              actual_outputs: ['/tmp/generated.md'],
              expected_deliverables: ['core_technologies_evidence.md'],
            },
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
    expect(screen.getByText('Execution completed, but verification failed')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getByText('Produced files (1)')).toBeInTheDocument();
    expect(screen.getByText('Expected deliverables (1)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Re-verify' }));
    expect(onReverify).toHaveBeenCalledTimes(1);
  });

  it('separates completed execution from published artifacts', () => {
    render(
      <ExecutionResult
        resultLoading={false}
        taskResult={{
          task_id: 19,
          status: 'completed',
          content: 'Collected recent review metadata.',
          metadata: {
            execution_status: 'completed',
            verification: {
              status: 'passed',
              checks_total: 2,
              checks_passed: 2,
              blocking: true,
              generated: false,
              failures: [],
            },
          },
        }}
        cachedResult={undefined}
      />
    );

    expect(screen.getByText('No published artifact')).toBeInTheDocument();
    expect(
      screen.getByText('Execution finished without a published artifact')
    ).toBeInTheDocument();
  });
});
