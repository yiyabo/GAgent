import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface CodeBlockProps {
  children: string;
  className?: string;
}

// 自定义代码块渲染
const CodeBlock: React.FC<CodeBlockProps> = ({ children, className }) => {
  const language = className?.replace('lang-', '') || 'text';

  return (
    <SyntaxHighlighter
      style={tomorrow}
      language={language}
      PreTag="div"
      customStyle={{
        margin: '8px 0',
        borderRadius: '6px',
        fontSize: '13px',
      }}
    >
      {children}
    </SyntaxHighlighter>
  );
};

export { CodeBlock };
export default CodeBlock;
