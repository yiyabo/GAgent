import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { ChatState } from './slices/types';
import { createSessionSlice } from './slices/createSessionSlice';
import { createMessageSlice } from './slices/createMessageSlice';
import { createUISlice } from './slices/createUISlice';
import { createContextSlice } from './slices/createContextSlice';
import { createMemorySlice } from './slices/createMemorySlice';
import { createFileSlice } from './slices/createFileSlice';

/**
 * Refactored Chat Store
 * 
 * The store is split into several slices for better maintainability:
 * - SessionSlice: Manages chat sessions and historical context.
 * - MessageSlice: Handles sending/receiving messages and streaming.
 * - UISlice: Manages input text, typing status, and UI visibility.
 * - ContextSlice: Synchronizes with plan/task execution context.
 * - MemorySlice: Integrates with RAG memory features.
 * - FileSlice: Manages attachments and file uploads.
 */
export const useChatStore = create<ChatState>()(
    subscribeWithSelector((set, get, store) => ({
        ...createSessionSlice(set, get, store),
        ...createMessageSlice(set, get, store),
        ...createUISlice(set, get, store),
        ...createContextSlice(set, get, store),
        ...createMemorySlice(set, get, store),
        ...createFileSlice(set, get, store),
    } as ChatState))
);
