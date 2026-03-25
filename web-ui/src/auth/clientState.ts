import { queryClient } from '@/queryClient';
import { useChatStore } from '@store/chat';
import { useMemoryStore } from '@store/memory';
import { useTasksStore } from '@store/tasks';
import { SessionStorage } from '@utils/sessionStorage';

export const resetClientStateForAuthChange = (): void => {
  SessionStorage.clearAll();
  queryClient.clear();
  useChatStore.setState(useChatStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
  useMemoryStore.setState(useMemoryStore.getInitialState(), true);
};
