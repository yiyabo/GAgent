import React, { useState, useRef } from 'react';
import {
  Input,
  Button,
  Typography,
} from 'antd';
import {
  SendOutlined,
  ExperimentOutlined,
  CompassOutlined,
  ThunderboltOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@store/auth';
import { useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';
import FileUploadButton from './FileUploadButton';
import ResponseStyleSelector from './ResponseStyleSelector';
import UploadedFilesList from './UploadedFilesList';

const { TextArea } = Input;

export interface HeroWelcomeProps {
  onSendMessage: (text: string) => Promise<void> | void;
  isProcessing?: boolean;
}

interface StarterPrompt {
  icon: React.ReactNode;
  title: TranslationKey;
  desc: TranslationKey;
  prompt: TranslationKey;
}

const STARTER_PROMPTS: StarterPrompt[] = [
  {
    icon: <ExperimentOutlined style={{ color: 'var(--status-success-fg)' }} />,
    title: 'hero.starter1.title',
    desc: 'hero.starter1.desc',
    prompt: 'hero.starter1.prompt',
  },
  {
    icon: <CompassOutlined style={{ color: 'var(--color-action)' }} />,
    title: 'hero.starter2.title',
    desc: 'hero.starter2.desc',
    prompt: 'hero.starter2.prompt',
  },
  {
    icon: <ThunderboltOutlined style={{ color: 'var(--status-warning-fg)' }} />,
    title: 'hero.starter3.title',
    desc: 'hero.starter3.desc',
    prompt: 'hero.starter3.prompt',
  },
  {
    icon: <ReadOutlined style={{ color: 'var(--status-blocked-fg)' }} />,
    title: 'hero.starter4.title',
    desc: 'hero.starter4.desc',
    prompt: 'hero.starter4.prompt',
  },
];

export const HeroWelcome: React.FC<HeroWelcomeProps> = ({
  onSendMessage,
  isProcessing = false,
}) => {
  const t = useT();
  const { user } = useAuthStore();
  const inputRef = useRef<any>(null);
  const [inputText, setInputText] = useState('');
  const [showCapabilities, setShowCapabilities] = useState(true);

  // Extract display name
  const displayName = React.useMemo(() => {
    const rawUser = user as any;
    if (rawUser?.username && typeof rawUser.username === 'string' && rawUser.username.trim()) {
      return rawUser.username;
    }
    if (user?.email) {
      return user.email.split('@')[0];
    }
    return t('hero.fallbackName');
  }, [user, t]);

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || isProcessing) return;
    onSendMessage(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStarterClick = (prompt: string) => {
    setInputText(prompt);
    inputRef.current?.focus();
  };

  return (
    <div className="hero-welcome-container">
      {/* 1. Header Greeting */}
      <div className="hero-welcome-header">
        <div className="hero-welcome-title-row">
          <span className="hero-welcome-logo">🌀</span>
          <h1 className="hero-welcome-title">
            {t('hero.greeting', { name: displayName })}
          </h1>
        </div>
        <p className="hero-welcome-subtitle">
          {t('hero.subtitle')}
        </p>
      </div>

      {/* 2. Main Hero Prompt Card */}
      <div className="hero-prompt-card">
        {/* Uploaded files chips inside card if any */}
        <div className="hero-prompt-files">
          <UploadedFilesList />
        </div>

        {/* Text Input Area */}
        <div className="hero-prompt-textarea-wrapper">
          <TextArea
            ref={inputRef}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('hero.placeholder')}
            autoSize={{ minRows: 4, maxRows: 10 }}
            className="hero-prompt-textarea"
            autoFocus
          />
        </div>

        {/* Card Bottom Controls Toolbar */}
        <div className="hero-prompt-toolbar">
          <div className="hero-prompt-toolbar-left">
            <FileUploadButton size="small" />
            <ResponseStyleSelector />
          </div>

          <div className="hero-prompt-toolbar-right">
            <Button
              type="primary"
              shape="circle"
              icon={<SendOutlined style={{ transform: 'rotate(-45deg)', marginLeft: 2 }} />}
              onClick={handleSend}
              disabled={!inputText.trim() || isProcessing}
              loading={isProcessing}
              className="hero-send-btn"
            />
          </div>
        </div>
      </div>

      {/* Clean Footnote / Capability Hint */}
      <div className="hero-welcome-footnote">
        {t('hero.footnote')}
      </div>

      {/* 3. Explore Capabilities / Starter Cards */}
      <div className="hero-capabilities-section">
        <div
          className="hero-capabilities-header"
          onClick={() => setShowCapabilities((prev) => !prev)}
        >
          <span className="hero-capabilities-arrow" style={{ transform: showCapabilities ? 'rotate(90deg)' : 'none' }}>
            ›
          </span>
          <span className="hero-capabilities-title">{t('hero.workflows')}</span>
        </div>

        {showCapabilities && (
          <div className="hero-starter-grid">
            {STARTER_PROMPTS.map((item, idx) => (
              <div
                key={idx}
                className="hero-starter-card"
                onClick={() => handleStarterClick(t(item.prompt))}
              >
                <div className="hero-starter-icon">{item.icon}</div>
                <div className="hero-starter-content">
                  <div className="hero-starter-title">{t(item.title)}</div>
                  <div className="hero-starter-desc">{t(item.desc)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default HeroWelcome;
