import type en from './locales/en.json';

type Flatten<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${Prefix}${K}` : Flatten<T[K], `${Prefix}${K}.`>;
}[keyof T & string];

/** Every key in the English catalogue, as dotted paths: 'header.ready' | 'common.cancel' | ... */
export type TranslationKey = Flatten<typeof en>;
