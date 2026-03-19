import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const RETRY_DELAY_MS = 600;
/** attempt indices 0..MAX_RETRY_INDEX inclusive => 6 total loads */
const MAX_RETRY_INDEX = 5;

/**
 * Full URL produced by buildArtifactFileUrl (…/artifacts/sessions/{id}/file?path=…).
 * External http(s) images are excluded from retry.
 */
export function isSessionArtifactFileUrl(url: string): boolean {
  if (!url || typeof url !== 'string') return false;
  if (!/^https?:\/\//i.test(url.trim())) return false;
  try {
    const path = new URL(url.trim()).pathname;
    return path.includes('/artifacts/sessions/') && path.endsWith('/file');
  } catch {
    return false;
  }
}

export interface SessionArtifactImageProps {
  url: string;
  alt?: string;
  /** Applied to the underlying img */
  imageStyle?: React.CSSProperties;
  loading?: 'lazy' | 'eager';
  decoding?: 'async' | 'auto' | 'sync';
}

/**
 * Image loader with bounded retries for session artifact file URLs (handles promote / race 404).
 */
export const SessionArtifactImage: React.FC<SessionArtifactImageProps> = ({
  url,
  alt = '',
  imageStyle,
  loading = 'lazy',
  decoding = 'async',
}) => {
  const [attempt, setAttempt] = useState(0);
  const [broken, setBroken] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setAttempt(0);
    setBroken(false);
  }, [url]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    },
    [],
  );

  const src = useMemo(() => {
    const u = url.trim();
    if (!isSessionArtifactFileUrl(u) || attempt === 0) {
      return u;
    }
    const sep = u.includes('?') ? '&' : '?';
    return `${u}${sep}_retry=${attempt}`;
  }, [url, attempt]);

  const handleError = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!isSessionArtifactFileUrl(url)) {
      setBroken(true);
      return;
    }
    if (attempt >= MAX_RETRY_INDEX) {
      setBroken(true);
      return;
    }
    timerRef.current = window.setTimeout(() => {
      setAttempt((a) => a + 1);
      timerRef.current = null;
    }, RETRY_DELAY_MS);
  }, [url, attempt]);

  if (!url.trim()) {
    return null;
  }

  if (broken) {
    return (
      <span
        style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', margin: '8px 0' }}
        title={url}
      >
        [Image failed to load] {alt || url}
      </span>
    );
  }

  return (
    <img
      key={`${url}_${attempt}`}
      src={src}
      alt={alt}
      loading={loading}
      decoding={decoding}
      onError={handleError}
      style={imageStyle}
    />
  );
};
