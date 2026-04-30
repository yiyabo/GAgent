import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

const wait = (delayMs: number): Promise<void> => new Promise((resolve) => {
  window.setTimeout(resolve, delayMs);
});

export const retryLazy = (
  importer: () => Promise<{ default: ComponentType }>,
  retries = 2,
  delayMs = 500,
): LazyExoticComponent<ComponentType> => lazy(async () => {
  let lastError: unknown;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await importer();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        await wait(delayMs);
      }
    }
  }

  throw lastError;
});
