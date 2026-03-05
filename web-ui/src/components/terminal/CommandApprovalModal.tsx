import React from 'react';
import { Alert, Modal, Typography } from 'antd';
import type { TerminalApprovalPayload } from '@/types';

const { Text, Paragraph } = Typography;

interface Props {
  open: boolean;
  approval: TerminalApprovalPayload | null;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}

const CommandApprovalModal: React.FC<Props> = ({ open, approval, onApprove, onReject }) => {
  const approvalId = approval?.approval_id || '';

  return (
    <Modal
      open={open}
      title="Command Approval Required"
      okText="Approve"
      cancelText="Reject"
      onOk={() => {
        if (approvalId) onApprove(approvalId);
      }}
      onCancel={() => {
        if (approvalId) onReject(approvalId);
      }}
      okButtonProps={{ danger: true }}
      destroyOnClose
    >
      <Alert
        type="warning"
        showIcon
        message="This command has been classified as forbidden and is blocked pending your decision."
        style={{ marginBottom: 12 }}
      />
      <Paragraph style={{ marginBottom: 8 }}>
        <Text strong>Command</Text>
      </Paragraph>
      <Paragraph code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
        {approval?.command || '-'}
      </Paragraph>
      <Paragraph style={{ marginBottom: 4 }}>
        <Text strong>Risk</Text>: {approval?.risk_level || '-'}
      </Paragraph>
      <Paragraph>
        <Text strong>Reason</Text>: {approval?.reason || '-'}
      </Paragraph>
    </Modal>
  );
};

export default CommandApprovalModal;
