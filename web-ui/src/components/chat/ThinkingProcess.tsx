import React, { useState, useEffect, useRef } from 'react';
import { ThinkingProcess as ThinkingProcessType, ThinkingStep } from '@/types';
import { CaretRightOutlined, LoadingOutlined, CheckCircleOutlined, InfoCircleOutlined, BulbOutlined, ToolOutlined } from '@ant-design/icons';
import { motion, AnimatePresence } from 'framer-motion';
import { theme, Tag, Tooltip } from 'antd';
import classNames from 'classnames';

interface ThinkingProcessProps {
    process: ThinkingProcessType;
    isFinished?: boolean;
}

const ThinkingStepItem: React.FC<{ step: ThinkingStep; isLast: boolean; isFinished?: boolean }> = ({ step, isLast, isFinished }) => {
    const { token } = theme.useToken();

    // Determine step type and icon
    const isTool = !!step.action;
    const isError = step.status === 'error';
    const isDone = step.status === 'done';

    let icon = <BulbOutlined style={{ color: token.colorTextSecondary }} />;
    if (isTool) icon = <ToolOutlined style={{ color: token.colorPrimary }} />;
    if (isError) icon = <InfoCircleOutlined style={{ color: token.colorError }} />;
    if (isDone) icon = <CheckCircleOutlined style={{ color: token.colorSuccess }} />;
    if (step.status === 'analyzing' || step.status === 'calling_tool') icon = <div className="animate-spin"><LoadingOutlined style={{ color: token.colorPrimary }} /></div>;

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
            className="mb-3 pl-4 border-l-2 relative"
            style={{
                borderColor: isLast && !isFinished ? token.colorPrimary : token.colorBorderSecondary,
            }}
        >
            {/* Timeline dot */}
            <div
                className="absolute -left-[9px] top-0 w-4 h-4 rounded-full flex items-center justify-center bg-white dark:bg-gray-800 border-2"
                style={{ borderColor: isLast && !isFinished ? token.colorPrimary : token.colorBorder }}
            >
                <div className="scale-75">{icon}</div>
            </div>

            <div className="text-sm">
                {/* Thought Content */}
                {step.thought && (
                    <div className="text-gray-600 dark:text-gray-300 mb-2 leading-relaxed">
                        {step.thought}
                    </div>
                )}

                {/* Action Card */}
                {isTool && actionDetails && (
                    <div className="mt-2 bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3 border border-gray-100 dark:border-gray-700/50">
                        <div className="flex items-center gap-2 mb-1">
                            <span className="font-mono text-xs font-semibold text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-1.5 py-0.5 rounded">
                                {actionDetails.tool}
                            </span>
                            <span className="text-xs text-gray-400">正在调用...</span>
                        </div>
                        <div className="font-mono text-xs text-gray-500 overflow-x-auto">
                            {JSON.stringify(actionDetails.params)}
                        </div>
                        {step.action_result && (
                            <div className="mt-2 text-xs border-t border-dashed pt-2 dark:border-gray-700">
                                <span className="text-green-600 font-medium">Result: </span>
                                <span className="text-gray-500 line-clamp-3">{step.action_result}</span>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </motion.div>
    );
};

export const ThinkingProcess: React.FC<ThinkingProcessProps> = ({ process, isFinished }) => {
    const [isExpanded, setIsExpanded] = useState(!isFinished);
    const { token } = theme.useToken();
    const contentRef = useRef<HTMLDivElement>(null);

    // Auto-expand when new steps come in if it acts like a stream
    useEffect(() => {
        if (!isFinished && process.status === 'active') {
            setIsExpanded(true);
        }
        // Auto-collapse when finished (optional, maybe keep expanded?)
        // if (isFinished) setIsExpanded(false);
    }, [process.steps.length, isFinished, process.status]);

    const duration = (process.total_iterations || process.steps.length) * 1.5; // mocking duration estimate

    return (
        <div className="my-4 max-w-3xl mx-auto">
            <motion.div
                className="rounded-xl overflow-hidden border bg-white dark:bg-[#1a1a1a]"
                style={{ borderColor: token.colorBorderSecondary }}
                initial={false}
            >
                {/* Header Toggle */}
                <div
                    onClick={() => setIsExpanded(!isExpanded)}
                    className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-white/5 transition-colors"
                >
                    <div className="flex items-center gap-3">
                        <div className={classNames("transition-transform duration-200", { "rotate-90": isExpanded })}>
                            <CaretRightOutlined style={{ fontSize: 12, color: token.colorTextTertiary }} />
                        </div>
                        <div className="flex items-center gap-2">
                            {process.status === 'active' ? (
                                <LoadingOutlined spin style={{ color: token.colorPrimary }} />
                            ) : (
                                <div className="w-4 h-4 rounded-full bg-gradient-to-tr from-blue-500 to-purple-500 flex items-center justify-center">
                                    <BulbOutlined style={{ color: '#fff', fontSize: 10 }} />
                                </div>
                            )}
                            <span className="font-medium text-sm text-gray-700 dark:text-gray-200">
                                {process.status === 'active' ? 'Deep Thinking...' : 'Thinking Process'}
                            </span>
                        </div>
                    </div>

                    <div className="flex items-center gap-3 text-xs text-gray-400">
                        {process.steps.length > 0 && <span>{process.steps.length} Steps</span>}
                        {/* <span>~{Math.round(duration)}s</span> */}
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
                            <div className="px-4 pb-4 pt-1 bg-gray-50/30 dark:bg-black/20 border-t border-gray-100 dark:border-gray-800">
                                <div className="pl-2 pt-3">
                                    {process.steps.map((step, idx) => (
                                        <ThinkingStepItem
                                            key={idx}
                                            step={step}
                                            isLast={idx === process.steps.length - 1}
                                            isFinished={isFinished}
                                        />
                                    ))}

                                    {process.status === 'active' && (
                                        <motion.div
                                            initial={{ opacity: 0 }}
                                            animate={{ opacity: 1 }}
                                            className="ml-4 pl-4 border-l-2 border-dashed border-gray-200 dark:border-gray-700 py-1"
                                        >
                                            <span className="text-xs text-gray-400 italic">Thinking about next step...</span>
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
