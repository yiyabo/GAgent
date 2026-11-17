import React, { useMemo, useState } from 'react';
import { Button } from 'antd';
import type { ButtonProps } from 'antd';
import { NodeIndexOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';
import type { PlanTaskRunInspectionTarget } from '@/types/runInspection';
import RunInspectorDrawer from './RunInspectorDrawer';
import { taskHasRun } from './model';

interface InspectTaskRunButtonProps {
  planId: number | null | undefined;
  taskId: number;
  taskName?: string | null;
  /** Pin a specific execution job; omit to inspect the task's latest run. */
  jobId?: string | null;
  status?: string | null;
  executionResult?: string | null;
  size?: ButtonProps['size'];
  type?: ButtonProps['type'];
  style?: React.CSSProperties;
}

/** Opens the read-only Run Inspector for the run that executed a plan task; hidden until the task has run. */
const InspectTaskRunButton: React.FC<InspectTaskRunButtonProps> = ({
  planId, taskId, taskName, jobId, status, executionResult, size = 'small', type = 'default', style,
}) => {
  const t = useT();
  const [open, setOpen] = useState(false);
  const target = useMemo<PlanTaskRunInspectionTarget | null>(() => (
    planId
      ? { planId, taskId, ...(jobId ? { jobId } : {}), ...(taskName ? { taskName } : {}) }
      : null
  ), [planId, taskId, jobId, taskName]);
  if (!target || !taskHasRun({ status, execution_result: executionResult })) return null;
  return (
    <>
      <Button size={size} type={type} style={style} icon={<NodeIndexOutlined />} onClick={() => setOpen(true)}>
        {t('runInspector.inspect')}
      </Button>
      <RunInspectorDrawer open={open} sessionId={null} target={target} onClose={() => setOpen(false)} />
    </>
  );
};

export default InspectTaskRunButton;
