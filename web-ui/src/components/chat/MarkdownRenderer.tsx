import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';
import 'katex/dist/katex.min.css';

interface MarkdownRendererProps {
    content: string;
    className?: string;
}

/**
 * Enhanced Markdown renderer with LaTeX math support.
 * 
 * Supports:
 * - Inline math: $E = mc^2$
 * - Block math: $$ \int_0^1 f(x) dx $$
 * - Code syntax highlighting
 * - All standard Markdown features
 */

/**
 * Preprocess content to normalize LaTeX delimiters.
 * Converts \[...\] and \(...\) to $$...$$ and $...$ respectively.
 */
const preprocessLaTeX = (content: string): string => {
    let processed = content;

    // 1. Fix commonly malformed block math ending with single $ (e.g. $$...$)
    // Matches $$ followed by anything not containing $, then ending with $ but not $$
    processed = processed.replace(/\$\$([^\$]+)\$(?!\$)/g, '$$$$1$$');

    // 2. Fix isolated $ used as currency or typo, if it looks like math
    // This is hard to distinguish perfectly, but we can try to fix clear cases
    // e.g. "Take $f(x)$" -> valid. "Cost is $10" -> valid (if not closed).

    // 3. Fix missing opening $ for common math symbols at start of line or after space
    // e.g. " \mathbb{H}$" -> " $\mathbb{H}$"
    processed = processed.replace(/(^|\s)(\\mathbb\{[A-Z]\}\$)/g, '$1$$$2');

    // 4. Convert \[...\] to $$...$$  (display math)
    processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, '$$$$1$$');

    // 5. Convert \(...\) to $...$  (inline math)
    processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$');

    // 6. Convert standalone [ formula ] on its own line to $$ formula $$
    // This handles cases like "[ f(z) = \frac{...} ]"
    // Be careful not to match regular brackets like [1] or [link]
    // using a stricter pattern: must contain backslash commands
    processed = processed.replace(/^\s*\[\s*([^[\]]*\\[a-zA-Z]+[^[\]]*)\s*\]\s*$/gm, '$$$$ $1 $$$$');

    // 7. Remove potential "currency" confusion if LLM outputs "$$1$" meaning $1
    // If we have $$1$ (which we fixed to $$1$$ above), it renders as 1 in display math mode.
    // If it was meant to be currency, context usually clarifies.

    return processed;
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, className }) => {
    // Preprocess content to normalize LaTeX delimiters
    const processedContent = preprocessLaTeX(content);

    return (
        <div className={className}>
            <ReactMarkdown
                remarkPlugins={[remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={{
                    // Code block with syntax highlighting
                    code(props) {
                        const { children, className: codeClassName, ...rest } = props;
                        const match = /language-(\w+)/.exec(codeClassName || '');
                        const language = match ? match[1] : 'text';
                        const isInline = !match;

                        if (!isInline && match) {
                            return (
                                <SyntaxHighlighter
                                    style={tomorrow as Record<string, React.CSSProperties>}
                                    language={language}
                                    PreTag="div"
                                    customStyle={{
                                        margin: '8px 0',
                                        borderRadius: '6px',
                                        fontSize: '13px',
                                    }}
                                >
                                    {String(children).replace(/\n$/, '')}
                                </SyntaxHighlighter>
                            );
                        }

                        // Inline code
                        return (
                            <code
                                className={codeClassName}
                                style={{
                                    backgroundColor: 'var(--bg-tertiary, #f5f5f5)',
                                    padding: '2px 6px',
                                    borderRadius: '4px',
                                    fontSize: '0.9em',
                                }}
                                {...rest}
                            >
                                {children}
                            </code>
                        );
                    },
                    // Paragraph styling
                    p({ children }) {
                        return <p style={{ margin: '0 0 8px 0', lineHeight: 1.6 }}>{children}</p>;
                    },
                    // List styling
                    ul({ children }) {
                        return <ul style={{ margin: '8px 0', paddingLeft: '20px' }}>{children}</ul>;
                    },
                    ol({ children }) {
                        return <ol style={{ margin: '8px 0', paddingLeft: '20px' }}>{children}</ol>;
                    },
                    // Blockquote styling
                    blockquote({ children }) {
                        return (
                            <blockquote
                                style={{
                                    borderLeft: '4px solid #d9d9d9',
                                    paddingLeft: '12px',
                                    margin: '8px 0',
                                    color: '#666',
                                    fontStyle: 'italic',
                                }}
                            >
                                {children}
                            </blockquote>
                        );
                    },
                    // Table styling
                    table({ children }) {
                        return (
                            <div style={{ overflowX: 'auto', margin: '8px 0' }}>
                                <table
                                    style={{
                                        borderCollapse: 'collapse',
                                        width: '100%',
                                        fontSize: '14px',
                                    }}
                                >
                                    {children}
                                </table>
                            </div>
                        );
                    },
                    th({ children }) {
                        return (
                            <th
                                style={{
                                    border: '1px solid var(--border-color, #e8e8e8)',
                                    padding: '8px 12px',
                                    backgroundColor: 'var(--bg-tertiary, #fafafa)',
                                    textAlign: 'left',
                                }}
                            >
                                {children}
                            </th>
                        );
                    },
                    td({ children }) {
                        return (
                            <td
                                style={{
                                    border: '1px solid var(--border-color, #e8e8e8)',
                                    padding: '8px 12px',
                                }}
                            >
                                {children}
                            </td>
                        );
                    },
                }}
            >
                {processedContent}
            </ReactMarkdown>
        </div>
    );
};

export default MarkdownRenderer;
