import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Input,
  Button,
  Switch,
  Tooltip,
  Dropdown,
  Typography,
  Space,
  Tag,
} from 'antd';
import {
  SendOutlined,
  PlusOutlined,
  DownOutlined,
  ThunderboltOutlined,
  ExperimentOutlined,
  CompassOutlined,
  ReadOutlined,
  RightOutlined,
  RocketOutlined,
  FileImageOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@store/auth';
import { useChatStore } from '@store/chat';
import FileUploadButton from './FileUploadButton';
import UploadedFilesList from './UploadedFilesList';

const { TextArea } = Input;
const { Title, Text, Paragraph } = Typography;

export interface HeroWelcomeProps {
  onSendMessage: (text: string, mode?: string) => Promise<void> | void;
  isProcessing?: boolean;
}

const STARTER_PROMPTS = [
  {
    icon: <ExperimentOutlined style={{ color: '#10b981' }} />,
    title: '噬菌体全基因组注释',
    desc: '预测开放阅读框(ORF)，注释结构与裂解基因，绘制环状拓扑图',
    prompt: '请帮我对上传的噬菌体 FASTA 基因组序列进行完整的结构与功能注释，识别裂解酶、穿孔素及尾部纤维蛋白，并生成基因组特征概览。',
  },
  {
    icon: <CompassOutlined style={{ color: '#3b82f6' }} />,
    title: '噬菌体-宿主互作预测',
    desc: '分析受体结合蛋白(RBP)与细菌特异性受体结合位点',
    prompt: '请分析目标噬菌体的受体结合蛋白 (RBP / Tail Fiber) 结构域，预测其对铜绿假单胞菌/鲍曼不动杆菌等临床耐药菌的宿主谱特异性。',
  },
  {
    icon: <ThunderboltOutlined style={{ color: '#f59e0b' }} />,
    title: '差异表达与转录组分析',
    desc: '运行 RNA-Seq 差异表达分析，输出火山图与主要富集通路',
    prompt: '请对感染噬菌体前后的宿主菌转录组数据执行差异表达分析 (DESeq2)，生成火山图并富集出早期与晚期关键应激反应通路。',
  },
  {
    icon: <ReadOutlined style={{ color: '#8b5cf6' }} />,
    title: '噬菌体疗法文献深度调研',
    desc: '检索全球权威文献库，生成耐药菌鸡尾酒配方调研报告',
    prompt: '请对超级耐药肺炎克雷伯菌 (CRKP) 的噬菌体鸡尾酒协同疗法进行深度文献调研，总结最新临床试验案例并梳理治疗方案建议。',
  },
];

const EXECUTION_MODES = [
  { key: 'standard', label: 'Standard (标准)', desc: '平衡速度与深度，适合常规对话与快速生信分析' },
  { key: 'deepthink', label: 'DeepThink (深度思考)', desc: '启用多步推理、文献论证与复杂生信管线自省' },
  { key: 'autonomous', label: 'Autonomous (全自主)', desc: '全自动目标分解、工具调度与错误自愈执行' },
];

export const HeroWelcome: React.FC<HeroWelcomeProps> = ({
  onSendMessage,
  isProcessing = false,
}) => {
  const { user } = useAuthStore();
  const inputRef = useRef<any>(null);
  const [inputText, setInputText] = useState('');
  const [autoMode, setAutoMode] = useState(true);
  const [selectedModeKey, setSelectedModeKey] = useState<string>('standard');
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
    return '研究员';
  }, [user]);

  const currentMode = EXECUTION_MODES.find((m) => m.key === selectedModeKey) || EXECUTION_MODES[0];

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || isProcessing) return;
    onSendMessage(trimmed, selectedModeKey);
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

  const modeMenu = {
    items: EXECUTION_MODES.map((m) => ({
      key: m.key,
      label: (
        <div style={{ padding: '4px 0' }}>
          <div style={{ fontWeight: 500, fontSize: 13, color: 'var(--text-primary)' }}>{m.label}</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{m.desc}</div>
        </div>
      ),
    })),
    onClick: ({ key }: { key: string }) => setSelectedModeKey(key),
  };

  return (
    <div className="hero-welcome-container">
      {/* 1. Header Greeting */}
      <div className="hero-welcome-header">
        <div className="hero-welcome-title-row">
          <span className="hero-welcome-logo">🌀</span>
          <h1 className="hero-welcome-title">
            你好，{displayName}
          </h1>
        </div>
        <p className="hero-welcome-subtitle">
          我是 Phage-Agent，您的虚拟研究协作者——专为推理、计算和迭代而设计。
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
            placeholder="今天我能帮您处理什么生物医学 / 噬菌体研究任务？"
            autoSize={{ minRows: 4, maxRows: 10 }}
            className="hero-prompt-textarea"
            autoFocus
          />
        </div>

        {/* Card Bottom Controls Toolbar */}
        <div className="hero-prompt-toolbar">
          <div className="hero-prompt-toolbar-left">
            <FileUploadButton size="small" />
            
            <div className="hero-prompt-toggle-group">
              <span className="hero-toggle-label">自动</span>
              <Tooltip title="自动模式：AI将全自主分解任务、调用生信工具执行并汇总交付物">
                <Switch
                  size="small"
                  checked={autoMode}
                  onChange={setAutoMode}
                  className="hero-mode-switch"
                />
              </Tooltip>
              <Tooltip title="什么是自动模式？系统将在后台沙箱中自适应扩展算力并自主运行分析流水线。">
                <span className="hero-toggle-help">?</span>
              </Tooltip>
            </div>
          </div>

          <div className="hero-prompt-toolbar-right">
            <Dropdown menu={modeMenu} trigger={['click']} placement="topRight">
              <Button size="small" className="hero-mode-dropdown-btn">
                <Space size={4}>
                  <span>{currentMode.label.split(' ')[0]}</span>
                  <DownOutlined style={{ fontSize: 10 }} />
                </Space>
              </Button>
            </Dropdown>

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

      {/* Footnote */}
      <div className="hero-welcome-footnote">
        我在<span className="hero-footnote-link">自动扩展的机器</span>上运行您的分析。
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
          <span className="hero-capabilities-title">EXPLORE PHAGE-AGENT CAPABILITIES</span>
        </div>

        {showCapabilities && (
          <div className="hero-starter-grid">
            {STARTER_PROMPTS.map((item, idx) => (
              <div
                key={idx}
                className="hero-starter-card"
                onClick={() => handleStarterClick(item.prompt)}
              >
                <div className="hero-starter-icon">{item.icon}</div>
                <div className="hero-starter-content">
                  <div className="hero-starter-title">{item.title}</div>
                  <div className="hero-starter-desc">{item.desc}</div>
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
