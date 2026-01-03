import React, { useState, useEffect, useRef } from 'react';
import { ThinkingProcess as ThinkingProcessType, ThinkingStep } from '@/types';
import { CaretRightOutlined, LoadingOutlined, CheckCircleOutlined, InfoCircleOutlined, BulbOutlined, ToolOutlined, SearchOutlined } from '@ant-design/icons';
import { motion, AnimatePresence } from 'framer-motion';
import { theme, Tag, Tooltip } from 'antd';
import classNames from 'classnames';

interface ThinkingProcessProps {
    process: ThinkingProcessType;
    isFinished?: boolean;
}

const ThinkingStepItem: React.FC<{ step: ThinkingStep; index: number; isLast: boolean; isFinished?: boolean }> = ({ step, index, isLast, isFinished }) => {
    // Determine step type and icon
    const isTool = !!step.action;
    const isError = step.status === 'error';
    // A step is considered done if: status is 'done', or it has action_result (tool finished), or it's not the last step
    const hasActionResult = !!step.action_result;
    const isStepComplete = step.status === 'done' || hasActionResult || (!isLast && step.status !== 'thinking' && step.status !== 'calling_tool');
    const isSearchTool = isTool && step.action?.toLowerCase().includes('search');

    // Determine icon based on state
    let icon;
    if (isError) {
        icon = <InfoCircleOutlined style={{ color: 'var(--error-color)' }} />;
    } else if (isStepComplete || isFinished) {
        icon = <CheckCircleOutlined style={{ color: 'var(--success-color)' }} />;
    } else if (step.status === 'calling_tool') {
        icon = <LoadingOutlined spin style={{ color: 'var(--primary-color)' }} />;
    } else if (step.status === 'thinking' || step.status === 'analyzing') {
        icon = <LoadingOutlined spin style={{ color: 'var(--primary-color)' }} />;
    } else if (isSearchTool) {
        icon = <SearchOutlined style={{ color: 'var(--primary-color)' }} />;
    } else if (isTool) {
        icon = <ToolOutlined style={{ color: 'var(--primary-color)' }} />;
    } else {
        icon = <BulbOutlined style={{ color: 'var(--primary-color)' }} />;
    }

    // Parse action if exists
    let actionDetails = null;
    if (step.action) {
        try {
            const parsed = JSON.parse(step.action);
            actionDetails = parsed;
        } catch (e) {
            actionDetails = { tool: 'unknown', params: step.action };
        }
    }

    return (
        <motion.div
            initial={{ opacity: 0, y: 10, height: 0 }}
            animate={{ opacity: 1, y: 0, height: 'auto' }}
            transition={{ duration: 0.3 }}
            style={{
                marginBottom: 16,
                paddingLeft: 16,
                borderLeft: '2px solid',
                borderColor: isLast && !isFinished && !isStepComplete ? 'var(--primary-color)' : 'var(--border-color)',
                position: 'relative',
            }}
        >
            {/* Timeline dot with step number */}
            <div
                style={{
                    position: 'absolute',
                    left: -13,
                    top: 0,
                    width: 24,
                    height: 24,
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: isStepComplete || isFinished ? 'var(--success-color)' : 'var(--bg-primary)',
                    border: isStepComplete || isFinished ? 'none' : '2px solid var(--border-color)',
                    fontSize: 12,
                    fontWeight: 600,
                    color: isStepComplete || isFinished ? '#fff' : 'var(--text-secondary)',
                }}
            >
                {isStepComplete || isFinished ? (
                    <CheckCircleOutlined style={{ color: '#fff', fontSize: 14 }} />
                ) : (
                    index + 1
                )}
            </div>

            {/* Thought Content */}
            {step.thought && (
                <div style={{
                    color: 'var(--text-primary)',
                    marginBottom: 8,
                    lineHeight: 1.7,
                    letterSpacing: '0.02em',
                }}>
                    {step.thought}
                </div>
            )}

            {/* Action Card */}
            {isTool && actionDetails && (
                <div style={{
                    marginTop: 8,
                    background: 'var(--bg-tertiary)',
                    borderRadius: 8,
                    padding: 12,
                    border: '1px solid var(--border-color)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <span style={{
                            fontFamily: 'monospace',
                            fontSize: 12,
                            fontWeight: 600,
                            color: 'var(--primary-color)',
                            background: 'rgba(201, 100, 66, 0.1)',
                            padding: '2px 8px',
                            borderRadius: 4,
                        }}>
                            {actionDetails.tool}
                        </span>
                        {step.status === 'calling_tool' && (
                            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>正在调用...</span>
                        )}
                    </div>
                    <div style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: 'var(--text-secondary)',
                        overflowX: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                    }}>
                        {typeof actionDetails.params === 'object'
                            ? JSON.stringify(actionDetails.params, null, 2)
                            : String(actionDetails.params)
                        }
                    </div>
                    {step.action_result && (
                        <div style={{
                            marginTop: 8,
                            fontSize: 12,
                            borderTop: '1px dashed var(--border-color)',
                            paddingTop: 8,
                        }}>
                            <span style={{ color: 'var(--success-color)', fontWeight: 500 }}>结果: </span>
                            <span style={{
                                color: 'var(--text-secondary)',
                                display: '-webkit-box',
                                WebkitLineClamp: 3,
                                WebkitBoxOrient: 'vertical',
                                overflow: 'hidden',
                            }}>
                                {step.action_result}
                            </span>
                        </div>
                    )}
                </div>
            )}
        </motion.div>
    );
};

export const ThinkingProcess: React.FC<ThinkingProcessProps> = ({ process, isFinished }) => {
    // Default to collapsed when finished, expanded when active
    const [isExpanded, setIsExpanded] = useState(!isFinished && process.status === 'active');
    const contentRef = useRef<HTMLDivElement>(null);

    // Auto-expand when new steps come in during active streaming
    useEffect(() => {
        if (!isFinished && process.status === 'active') {
            setIsExpanded(true);
        }
        // Auto-collapse when finished
        if (isFinished && process.status !== 'active') {
            setIsExpanded(false);
        }
    }, [process.steps.length, isFinished, process.status]);

    return (
        <div style={{
            margin: '16px 0',
            maxWidth: '100%',
        }}>
            <motion.div
                style={{
                    borderRadius: 12,
                    overflow: 'hidden',
                    border: '1px solid var(--border-color)',
                    background: 'var(--bg-primary)',
                    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
                }}
                initial={false}
            >
                {/* Header Toggle */}
                <div
                    onClick={() => setIsExpanded(!isExpanded)}
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '12px 16px',
                        cursor: 'pointer',
                        background: 'var(--bg-primary)',
                        transition: 'background 0.2s ease',
                    }}
                    onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'var(--bg-tertiary)';
                    }}
                    onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'var(--bg-primary)';
                    }}
                >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                        }}>
                            {process.status === 'active' ? (
                                <LoadingOutlined spin style={{ color: 'var(--primary-color)', fontSize: 14 }} />
                            ) : (
                                <div style={{
                                    width: 16,
                                    height: 16,
                                    borderRadius: '50%',
                                    background: 'linear-gradient(135deg, var(--primary-color) 0%, #d4886e 100%)',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                }}>
                                    <BulbOutlined style={{ color: '#fff', fontSize: 10 }} />
                                </div>
                            )}
                            <span style={{
                                fontWeight: 500,
                                fontSize: 14,
                                color: 'var(--text-primary)',
                            }}>
                                {process.status === 'active' ? 'Thinking...' : 'Thought process'}
                            </span>
                        </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        {process.steps.length > 0 && (
                            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                                {process.steps.length} step{process.steps.length > 1 ? 's' : ''}
                            </span>
                        )}
                        <div style={{
                            transition: 'transform 0.2s ease',
                            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                        }}>
                            <CaretRightOutlined style={{ fontSize: 12, color: 'var(--text-tertiary)' }} />
                        </div>
                    </div>
                </div>

                {/* Content Area */}
                <AnimatePresence initial={false}>
                    {isExpanded && (
                        <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.3, ease: "easeInOut" }}
                        >
                            <div style={{
                                padding: '12px 16px 16px',
                                background: 'var(--bg-tertiary)',
                                borderTop: '1px solid var(--border-color)',
                                maxHeight: '400px',
                                overflowY: 'auto',
                            }}>
                                <div style={{ paddingLeft: 8, paddingTop: 8 }}>
                                    {process.steps.map((step, idx) => (
                                        <ThinkingStepItem
                                            key={idx}
                                            step={step}
                                            index={idx}
                                            isLast={idx === process.steps.length - 1}
                                            isFinished={isFinished}
                                        />
                                    ))}

                                    {process.status === 'active' && (
                                        <motion.div
                                            initial={{ opacity: 0 }}
                                            animate={{ opacity: 1 }}
                                            style={{
                                                marginLeft: 16,
                                                paddingLeft: 16,
                                                borderLeft: '2px dashed var(--border-color)',
                                                paddingTop: 4,
                                                paddingBottom: 4,
                                            }}
                                        >
                                            <span style={{
                                                fontSize: 12,
                                                color: 'var(--text-tertiary)',
                                                fontStyle: 'italic',
                                            }}>
                                                Thinking about next step...
                                            </span>
                                        </motion.div>
                                    )}
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </motion.div>
        </div>
    );
};
