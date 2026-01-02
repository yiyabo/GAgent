import React from 'react';
import { RobotOutlined } from '@ant-design/icons';
import './TypingIndicator.css';

interface TypingIndicatorProps {
    /**
     * Optional message to display alongside the animation.
     * @default "思考中"
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
    message = '思考中',
    showAvatar = true,
}) => {
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
                {message && <span className="typing-indicator-text">{message}</span>}
            </div>
        </div>
    );
};

export default TypingIndicator;
