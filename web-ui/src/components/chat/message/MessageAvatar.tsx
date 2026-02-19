import React from 'react';
import { Avatar } from 'antd';
import {
  UserOutlined,
  RobotOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';

interface MessageAvatarProps {
  type: 'user' | 'assistant' | 'system';
}

// 渲染头像 - Claude Code 风格
const MessageAvatar: React.FC<MessageAvatarProps> = ({ type }) => {
  const avatarProps = {
    size: 28 as const,
    style: { flexShrink: 0 },
  };

  switch (type) {
    case 'user':
      return (
        <Avatar
          {...avatarProps}
          icon={<UserOutlined />}
          style={{
            ...avatarProps.style,
            background: 'var(--bg-tertiary)',
            borderRadius: 4,
          }}
        />
      );
    case 'assistant':
      return (
        <Avatar
          {...avatarProps}
          icon={<RobotOutlined />}
          style={{
            ...avatarProps.style,
            background: 'var(--primary-gradient)',
            borderRadius: 6,
          }}
        />
      );
    case 'system':
      return (
        <Avatar
          {...avatarProps}
          icon={<InfoCircleOutlined />}
          style={{
            ...avatarProps.style,
            background: 'var(--bg-tertiary)',
          }}
        />
      );
    default:
      return null;
  }
};

export { MessageAvatar };
export default MessageAvatar;
