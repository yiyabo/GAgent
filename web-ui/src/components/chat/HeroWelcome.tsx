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
import FileUploadButton from './FileUploadButton';
import UploadedFilesList from './UploadedFilesList';

const { TextArea } = Input;

export interface HeroWelcomeProps {
  onSendMessage: (text: string) => Promise<void> | void;
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
    desc: '检索权威文献库，梳理耐药菌鸡尾酒配方与临床案例',
    prompt: '请对超级耐药肺炎克雷伯菌 (CRKP) 的噬菌体鸡尾酒协同疗法进行深度文献调研，总结最新临床试验案例并梳理治疗方案建议。',
  },
];

export const HeroWelcome: React.FC<HeroWelcomeProps> = ({
  onSendMessage,
  isProcessing = false,
}) => {
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
    return '研究员';
  }, [user]);

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
            你好，{displayName}
          </h1>
        </div>
        <p className="hero-welcome-subtitle">
          我是 Phage-Agent，专为噬菌体与生物医学研究设计的自主智能体。
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
            placeholder="输入您的研究目标或生信分析需求... (支持拖放 / 粘贴文件与序列)"
            autoSize={{ minRows: 4, maxRows: 10 }}
            className="hero-prompt-textarea"
            autoFocus
          />
        </div>

        {/* Card Bottom Controls Toolbar */}
        <div className="hero-prompt-toolbar">
          <div className="hero-prompt-toolbar-left">
            <FileUploadButton size="small" />
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
        支持生信工具链自主调度、噬菌体多模态分析与出版级图表生成
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
          <span className="hero-capabilities-title">常用研究工作流 / WORKFLOWS</span>
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
