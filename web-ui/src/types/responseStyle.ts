import type { TranslationKey } from '@/i18n/keys';

export const RESPONSE_STYLES: ReadonlyArray<{
  value: 'general' | 'product_manager' | 'senior_developer' | 'research_advisor';
  label: TranslationKey;
  description: TranslationKey;
}> = [
  { value: 'general', label: 'responseStyle.general.label', description: 'responseStyle.general.description' },
  { value: 'product_manager', label: 'responseStyle.productManager.label', description: 'responseStyle.productManager.description' },
  { value: 'senior_developer', label: 'responseStyle.seniorDeveloper.label', description: 'responseStyle.seniorDeveloper.description' },
  { value: 'research_advisor', label: 'responseStyle.researchAdvisor.label', description: 'responseStyle.researchAdvisor.description' },
];
export type ResponseStyle = typeof RESPONSE_STYLES[number]['value'];
export type ResponseLength = 'auto' | 'concise' | 'detailed';
export interface ResponsePreferences {
  response_style: ResponseStyle;
  response_length: ResponseLength;
}
export const DEFAULT_RESPONSE_PREFERENCES: ResponsePreferences = {
  response_style: 'general', response_length: 'auto',
};
