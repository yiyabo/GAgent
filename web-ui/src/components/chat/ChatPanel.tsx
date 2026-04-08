import React, { useRef, useEffect, useState, useCallback } from 'react';
import { App as AntdApp, Card, Input, Button, Space, Typography, Avatar, Divider, Tooltip, Select } from 'antd';
import {
  SendOutlined,
  PaperClipOutlined,
  ReloadOutlined,
  ClearOutlined,
  RobotOutlined,
  UserOutlined,
  MessageOutlined,
  InboxOutlined,
  FileImageOutlined,
  FilePdfOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { shallow } from 'zustand/shallow';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { useTasksStore } from '@store/tasks';
import ChatMessage from './ChatMessage';
import FileUploadButton from './FileUploadButton';
import UploadedFilesList from './UploadedFilesList';

const { TextArea } = Input;
const { Title, Text } = Typography;

// ---------------------------------------------------------------------------
// Helpers for paste / drag-drop
// ---------------------------------------------------------------------------

/** Allowed file extensions for drag-drop (same as FileUploadButton). */
const DROP_ALLOWED_EXTENSIONS = [
  '.pdf', '.doc', '.docx', '.txt', '.md', '.rtf', '.csv',
  '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff',
  '.zip', '.tar', '.tar.gz', '.tgz', '.gz',
  '.h5', '.hdf5', '.hdf', '.hd5', '.pdb', '.dcm', '.nii', '.npz', '.npy',
  '.fasta', '.fa', '.fna', '.faa', '.ffn', '.frn',
  '.fastq', '.fq', '.gff', '.gff3', '.gtf',
  '.vcf', '.sam', '.bam', '.bed',
  '.genbank', '.gb', '.gbk', '.embl',
  '.phy', '.phylip', '.nwk', '.newick', '.aln', '.clustal',
];

const DROP_ALLOWED_MIMES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain', 'text/markdown', 'text/csv',
  'application/rtf',
  'application/zip', 'application/x-zip-compressed',
  'application/x-tar', 'application/gzip', 'application/x-gzip',
  'application/octet-stream',
]);

function isFileAllowed(file: File): boolean {
  if (file.type.startsWith('image/')) return true;
  if (DROP_ALLOWED_MIMES.has(file.type)) return true;
  const name = file.name.toLowerCase();
  return DROP_ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/** Extract images from clipboard (paste event). */
function extractPasteImages(clipboardData: DataTransfer): File[] {
  const files: File[] = [];
  for (let i = 0; i < clipboardData.items.length; i++) {
    const item = clipboardData.items[i];
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) {
        // Give pasted images a meaningful name
        const ext = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const named = new File([file], `pasted-image-${timestamp}.${ext}`, { type: file.type });
        files.push(named);
      }
    }
  }
  return files;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const ChatPanel: React.FC = () => {
  const { message } = AntdApp.useApp();
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const scrollRafRef = useRef<number | null>(null);
  const inputRef = useRef<any>(null);
  const dragCounterRef = useRef(0);

  // Drag-drop visual state
  const [isDragOver, setIsDragOver] = useState(false);
  // Paste feedback
  const [pasteUploading, setPasteUploading] = useState(false);

  const {
    messages,
    inputText,
    isProcessing,
    isTyping,
    chatPanelVisible,
    setInputText,
    sendMessage,
    clearMessages,
    retryLastMessage,
    currentSession,
    defaultSearchProvider,
    setDefaultSearchProvider,
    isUpdatingProvider,
    defaultBaseModel,
    setDefaultBaseModel,
    isUpdatingBaseModel,
    defaultLLMProvider,
    setDefaultLLMProvider,
    isUpdatingLLMProvider,
    uploadFile,
  } = useChatStore(
    (state) => ({
      messages: state.messages,
      inputText: state.inputText,
      isProcessing: state.processingSessionIds.has(
        resolveChatSessionProcessingKey(state.currentSession)
      ),
      isTyping: state.isTyping,
      chatPanelVisible: state.chatPanelVisible,
      setInputText: state.setInputText,
      sendMessage: state.sendMessage,
      clearMessages: state.clearMessages,
      retryLastMessage: state.retryLastMessage,
      currentSession: state.currentSession,
      defaultSearchProvider: state.defaultSearchProvider,
      setDefaultSearchProvider: state.setDefaultSearchProvider,
      isUpdatingProvider: state.isUpdatingProvider,
      defaultBaseModel: state.defaultBaseModel,
      setDefaultBaseModel: state.setDefaultBaseModel,
      isUpdatingBaseModel: state.isUpdatingBaseModel,
      defaultLLMProvider: state.defaultLLMProvider,
      setDefaultLLMProvider: state.setDefaultLLMProvider,
      isUpdatingLLMProvider: state.isUpdatingLLMProvider,
      uploadFile: state.uploadFile,
    }),
    shallow
  );

  const { selectedTask, currentPlan } = useTasksStore();

  // ---- Prevent browser from opening dropped files globally ----
  // Without this, dropping a file anywhere on the page causes the browser
  // to navigate to the file (e.g. open PDF in a new tab).

  useEffect(() => {
    const preventBrowserDrop = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };
    // Must prevent both dragover AND drop at window level
    window.addEventListener('dragover', preventBrowserDrop);
    window.addEventListener('drop', preventBrowserDrop);
    return () => {
      window.removeEventListener('dragover', preventBrowserDrop);
      window.removeEventListener('drop', preventBrowserDrop);
    };
  }, []);

  // ---- Scroll management ----

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const updateAutoScroll = () => {
      const distanceToBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      autoScrollRef.current = distanceToBottom < 120;
    };

    updateAutoScroll();
    container.addEventListener('scroll', updateAutoScroll, { passive: true });
    return () => {
      container.removeEventListener('scroll', updateAutoScroll);
    };
  }, []);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container || !autoScrollRef.current || scrollRafRef.current !== null) return;
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = null;
      if (!autoScrollRef.current) return;
      container.scrollTo({
        top: container.scrollHeight,
        behavior: isProcessing ? 'auto' : 'smooth',
      });
    });
  }, [messages, isProcessing]);

  // ---- Upload helper ----

  const doUploadFile = useCallback(async (file: File) => {
    if (!currentSession) {
      message.error('请先创建或选择一个会话');
      return;
    }
    try {
      await uploadFile(file);
      message.success(`${file.name} 上传成功`);
    } catch (error: any) {
      message.error(`上传失败: ${error.message || '未知错误'}`);
    }
  }, [currentSession, uploadFile, message]);

  // ---- Paste handler (images) ----

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (!e.clipboardData) return;
    const images = extractPasteImages(e.clipboardData);
    if (images.length === 0) return; // Let normal text paste proceed

    e.preventDefault(); // Prevent pasting image data as text
    setPasteUploading(true);

    Promise.all(images.map((img) => doUploadFile(img)))
      .finally(() => setPasteUploading(false));
  }, [doUploadFile]);

  // ---- Drag & Drop handlers ----

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer?.types?.includes('Files')) {
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length === 0) return;

    const allowed: File[] = [];
    const rejected: string[] = [];

    for (const file of files) {
      if (isFileAllowed(file)) {
        allowed.push(file);
      } else {
        rejected.push(file.name);
      }
    }

    if (rejected.length > 0) {
      message.warning(`不支持的文件类型: ${rejected.join(', ')}`);
    }

    if (allowed.length > 0) {
      Promise.all(allowed.map((f) => doUploadFile(f)));
    }
  }, [doUploadFile, message]);

  // ---- Message handlers ----

  const handleSendMessage = async () => {
    const draft = inputText.trim();
    if (!draft || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id,
      plan_title: currentPlan || undefined,
    };

    setInputText('');
    try {
      await sendMessage(draft, metadata);
    } catch (error) {
      setInputText(draft);
      message.error('Failed to send message. Please try again.');
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
  };

  // Quick actions.
  const handleQuickAction = (action: string) => {
    const quickMessages = {
      create_plan: 'Help me create a new plan',
      list_tasks: 'Show all current tasks',
      system_status: 'Check system status',
      help: 'I need help. Tell me what you can do',
    };

    const msg = quickMessages[action as keyof typeof quickMessages];
    if (msg) {
      setInputText(msg);
      inputRef.current?.focus();
    }
  };

  const handleProviderChange = async (value: string | undefined) => {
    try {
      await setDefaultSearchProvider(
        (value as 'builtin' | 'perplexity' | 'tavily') ?? null
      );
    } catch (error) {
      console.error('Failed to switch search provider:', error);
      message.error('Failed to switch search provider. Please try again later.');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    try {
      await setDefaultBaseModel(
        (value as 'qwen3.6-plus' | 'qwen3.5-plus' | 'qwen3-max-2026-01-23' | 'qwen-turbo') ?? null
      );
    } catch (error) {
      console.error('Failed to switch base model:', error);
      message.error('Failed to switch base model. Please try again later.');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    try {
      await setDefaultLLMProvider(
        (value as 'qwen') ?? null
      );
    } catch (error) {
      console.error('Failed to switch LLM provider:', error);
      message.error('Failed to switch LLM provider. Please try again later.');
    }
  };

  const providerOptions = [
    { label: 'Built-in Search', value: 'builtin' },
    { label: 'Perplexity Search', value: 'perplexity' },
    { label: 'Tavily MCP Search', value: 'tavily' },
  ];

  const providerValue = defaultSearchProvider ?? undefined;
  const baseModelValue = defaultBaseModel ?? undefined;
  const llmProviderValue = defaultLLMProvider ?? undefined;

  const llmProviderOptions = [
    { label: 'Qwen', value: 'qwen' },
  ];

  const baseModelOptions = [
    { label: 'Qwen3.6-Plus', value: 'qwen3.6-plus' },
    { label: 'Qwen3.5-Plus', value: 'qwen3.5-plus' },
    { label: 'Qwen3-Max (2026-01-23)', value: 'qwen3-max-2026-01-23' },
    { label: 'Qwen-Turbo', value: 'qwen-turbo' },
  ];

  if (!chatPanelVisible) {
    return null;
  }

  return (
    <div
      className="chat-panel"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="chat-drop-overlay">
          <div className="chat-drop-overlay-content">
            <InboxOutlined style={{ fontSize: 40, color: 'var(--primary-color)' }} />
            <div className="chat-drop-overlay-title">
              松开以上传文件
            </div>
            <div className="chat-drop-overlay-hint">
              支持图片、PDF、文档等文件
            </div>
          </div>
        </div>
      )}

      {/* Chat header */}
      <div className="chat-header">
        <Space align="center">
          <Avatar icon={<RobotOutlined />} size="small" />
          <div>
            <Title level={5} style={{ margin: 0 }}>
              AI Task Orchestration Assistant
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Online
            </Text>
          </div>
        </Space>

        <Space size="small">
          <Select
            size="small"
            value={providerValue}
            onChange={handleProviderChange}
            options={providerOptions}
            style={{ width: 140 }}
            placeholder="Search provider"
            disabled={isUpdatingProvider}
          />
          <Select
            size="small"
            value={llmProviderValue}
            onChange={handleLLMProviderChange}
            options={llmProviderOptions}
            style={{ width: 120 }}
            placeholder="LLM provider"
            disabled={isUpdatingLLMProvider}
          />
          <Select
            size="small"
            value={baseModelValue}
            onChange={handleBaseModelChange}
            options={baseModelOptions}
            style={{ width: 140 }}
            placeholder="Base model"
            disabled={isUpdatingBaseModel}
          />
          <Tooltip title="Clear chat">
            <Button
              type="text"
              size="small"
              icon={<ClearOutlined />}
              onClick={clearMessages}
            />
          </Tooltip>
        </Space>
      </div>

      {/* Message list */}
      <div className="chat-messages" ref={messagesContainerRef}>
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-tertiary)' }}>
            <MessageOutlined style={{ fontSize: 32, marginBottom: 16, color: 'var(--primary-color)' }} />
            <div>
              <Text style={{ color: 'var(--text-primary)' }}>Hello! I am your AI Task Orchestration Assistant.</Text>
            </div>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                I can help you create plans, manage tasks, and orchestrate execution.
              </Text>
            </div>

            {/* Quick action buttons */}
            <div style={{ marginTop: 16 }}>
              <Space direction="vertical" size="small">
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('create_plan')}
                >
                  Create a new plan
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('list_tasks')}
                >
                  View task list
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('system_status')}
                >
                  System status
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('help')}
                >
                  Help
                </Button>
              </Space>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={message}
                sessionId={currentSession?.session_id ?? currentSession?.id ?? null}
              />
            ))}
          </>
        )}
      </div>

      {/* Context info */}
      {currentPlan && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ padding: '0 16px 8px', fontSize: 12, color: 'var(--text-secondary)' }}>
            Current plan: {currentPlan}
          </div>
        </>
      )}

      {/* Input area */}
      <div className="chat-input-area">
        {/* Uploaded files list */}
        <UploadedFilesList />

        {/* Paste uploading indicator */}
        {pasteUploading && (
          <div style={{ padding: '4px 0', fontSize: 12, color: 'var(--primary-color)' }}>
            <FileImageOutlined style={{ marginRight: 4 }} />
            正在上传粘贴的图片...
          </div>
        )}

        <div className="chat-input-main" style={{ alignItems: 'stretch' }}>
          {/* Upload button */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center', padding: '6px 8px' }}>
            <FileUploadButton size="small" />
          </div>

          <TextArea
            ref={inputRef}
            value={inputText}
            onChange={handleInputChange}
            onKeyPress={handleKeyPress}
            onPaste={handlePaste}
            placeholder="输入消息... (可粘贴图片 / 拖放文件)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isProcessing}
            style={{ flex: 1, margin: 0 }}
          />

          {/* Send button */}
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSendMessage}
            disabled={!inputText.trim() || isProcessing}
            loading={isProcessing}
            style={{ height: 'auto', minHeight: 36, alignSelf: 'center' }}
          >
            Send
          </Button>
        </div>

        {/* Drop hint text in input area */}
        <div className="chat-input-hint">
          <Text type="secondary" style={{ fontSize: 11 }}>
            Ctrl+V 粘贴图片 | 拖放文件到此处上传
          </Text>
        </div>
      </div>
    </div>
  );
};

export default ChatPanel;
