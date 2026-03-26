export type ThinkingLanguage = 'zh' | 'en';

const CJK_CHAR_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const PUNCTUATION_CHARS = new Set([
  '\n',
  '。',
  '！',
  '？',
  '!',
  '?',
  '；',
  ';',
  '，',
  ',',
  '、',
  ':',
  '：',
]);
const INLINE_SPACE_CHARS = new Set([' ', '\t']);

export const THINKING_DELTA_FLUSH_INTERVAL_MS = 48;
export const THINKING_DELTA_MAX_WAIT_MS = 220;

export function inferThinkingLanguage(value: unknown): ThinkingLanguage {
  return CJK_CHAR_RE.test(String(value ?? '')) ? 'zh' : 'en';
}

export function localizeThinkingText(language: ThinkingLanguage, zh: string, en: string): string {
  return language === 'zh' ? zh : en;
}

export function defaultThinkingDisplayText(
  iteration: number,
  language: ThinkingLanguage
): string {
  if (iteration <= 0) {
    return localizeThinkingText(language, '准备整理回复', 'Preparing the response');
  }
  return localizeThinkingText(language, '分析当前步骤', 'Working through the current step');
}

function isCjkChar(value: string): boolean {
  return CJK_CHAR_RE.test(value);
}

function isAsciiWordChar(value: string): boolean {
  return /[A-Za-z0-9]/.test(value);
}

function resolveChunkLimit(characters: string[]): number {
  const length = characters.length;
  const firstVisibleChar = characters.find((value) => value.trim().length > 0) ?? characters[0] ?? '';
  const cjkMode = isCjkChar(firstVisibleChar);

  if (length >= 180) {
    return cjkMode ? 18 : 26;
  }
  if (length >= 120) {
    return cjkMode ? 14 : 20;
  }
  if (length >= 72) {
    return cjkMode ? 10 : 14;
  }
  if (length >= 36) {
    return cjkMode ? 6 : 10;
  }
  return cjkMode ? 3 : 6;
}

export function extractThinkingFlushChunk(
  buffer: string,
  force: boolean = false
): { flushable: string; remaining: string } {
  const text = String(buffer || '');
  const characters = Array.from(text);
  if (characters.length === 0) {
    return { flushable: '', remaining: '' };
  }
  if (force) {
    return { flushable: text, remaining: '' };
  }

  if (characters[0] === '\n' && characters[1] === '\n') {
    return {
      flushable: '\n\n',
      remaining: characters.slice(2).join(''),
    };
  }

  const chunkLimit = resolveChunkLimit(characters);
  let end = 0;
  let visibleCount = 0;

  while (end < characters.length) {
    const current = characters[end];
    end += 1;
    visibleCount += 1;

    if (current === '\n') {
      while (end < characters.length && characters[end] === '\n') {
        end += 1;
      }
      break;
    }

    if (PUNCTUATION_CHARS.has(current)) {
      while (end < characters.length && INLINE_SPACE_CHARS.has(characters[end])) {
        end += 1;
      }
      break;
    }

    if (visibleCount >= chunkLimit) {
      if (isAsciiWordChar(current)) {
        while (end < characters.length && isAsciiWordChar(characters[end])) {
          end += 1;
        }
      }
      while (end < characters.length && INLINE_SPACE_CHARS.has(characters[end])) {
        end += 1;
      }
      break;
    }
  }

  return {
    flushable: characters.slice(0, end).join(''),
    remaining: characters.slice(end).join(''),
  };
}
