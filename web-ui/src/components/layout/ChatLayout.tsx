import React from 'react';
import { useLayoutStore } from '@store/layout';
import ClassicChatLayout from './ClassicChatLayout';
import ResearchWorkspace from './ResearchWorkspace';

const ChatLayout: React.FC = () => {
  const mode = useLayoutStore((state) => state.workspaceMode);
  return mode === 'classic' ? <ClassicChatLayout /> : <ResearchWorkspace />;
};

export default ChatLayout;
