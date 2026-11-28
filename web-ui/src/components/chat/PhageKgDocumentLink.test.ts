import { describe, expect, it } from 'vitest';
import { linkifyPhageKgCitations, PHAGE_KG_DOC_HREF_PREFIX } from './PhageKgDocumentLink';

const ID = `doc-${'2aab6f085b684c0f7f629d9a09ab492a'}`;

describe('linkifyPhageKgCitations', () => {
  it('links bracketed and bare citations, mapping chunk ids to their paper', () => {
    const out = linkifyPhageKgCitations(`LysK has two domains [${ID}]; see ${ID}-chunk-004.`);
    expect(out).toBe(
      `LysK has two domains [[${ID}](${PHAGE_KG_DOC_HREF_PREFIX}${ID})]; see [${ID}](${PHAGE_KG_DOC_HREF_PREFIX}${ID}).`,
    );
  });

  it('links inline code that holds only citations', () => {
    const link = `[${ID}](${PHAGE_KG_DOC_HREF_PREFIX}${ID})`;
    expect(linkifyPhageKgCitations(`See \`${ID}\` and \`[${ID}]\`.`)).toBe(`See ${link} and [${link}].`);
    expect(linkifyPhageKgCitations(`\`${ID}-chunk-003, ${ID}\``)).toBe(`${link}, ${link}`);
  });

  it('links an abbreviated id to the one full id it matches in the message', () => {
    const other = `doc-${'2aab6f08'}${'0'.repeat(24)}`;
    const out = linkifyPhageKgCitations(`[${ID}] then (doc-2aab6f085b…) and doc-2aab6f08… and doc-ffffff…`);
    expect(out).toContain(`(${'[doc-2aab6f085b…]'}(${PHAGE_KG_DOC_HREF_PREFIX}${ID}))`);
    expect(out).toContain(' and doc-ffffff…');
    const ambiguous = linkifyPhageKgCitations(`${ID} ${other} doc-2aab6f08…`);
    expect(ambiguous.endsWith(' doc-2aab6f08…')).toBe(true);
  });

  it('leaves other code, existing links and malformed ids untouched', () => {
    const text = `\`id=${ID}\`\n\`\`\`\n${ID}\n\`\`\`\n[x](/files/${ID}) doc-123 x${ID}`;
    expect(linkifyPhageKgCitations(text)).toBe(text);
  });
});
