import React from 'react';
import { RobotOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';
import './TypingIndicator.css';

interface TypingIndicatorProps {
    /**
     * Optional message to display alongside the animation.
     * @default the localized "Thinking..."
     */
    message?: string;
    /**
     * Whether to show the AI avatar.
     * @default true
     */
    showAvatar?: boolean;
}

/**
 * A typing indicator component that displays an animated
 * "thinking" animation while the AI is preparing a response.
 */
export const TypingIndicator: React.FC<TypingIndicatorProps> = ({
    message,
    showAvatar = true,
}) => {
    const t = useT();
    const text = message === undefined ? t('chat.thinking') : message;
    return (
        <div className="typing-indicator-wrapper">
            {showAvatar && (
                <div className="typing-indicator-avatar">
                    <RobotOutlined style={{ fontSize: 16, color: 'var(--primary-color)' }} />
                </div>
            )}
            <div className="typing-indicator-bubble">
                <div className="typing-indicator-dots">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                </div>
                {text && <span className="typing-indicator-text">{text}</span>}
            </div>
        </div>
    );
};

export default TypingIndicator;
