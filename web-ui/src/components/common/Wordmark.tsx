import React from 'react';

export const PRODUCT_NAME = 'Phage-Agent';

/**
 * The product name in two colours: "Phage-" in the brand navy, "Agent" in the action blue.
 * Both come from tokens, so the pair stays distinct in the dark theme too. The visible text is the
 * plain name, so copy/paste and screen readers get "Phage-Agent".
 */
const Wordmark: React.FC<{ className?: string; style?: React.CSSProperties }> = ({ className, style }) => (
  <span className={`wordmark${className ? ` ${className}` : ''}`} style={style}>
    <span className="wordmark-phage">Phage-</span>
    <span className="wordmark-agent">Agent</span>
  </span>
);

export default Wordmark;
