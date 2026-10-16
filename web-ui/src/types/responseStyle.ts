export const RESPONSE_STYLES = [
  { value: 'general', label: '通用助手', description: '直接自然，按问题调整详略' },
  { value: 'product_manager', label: '产品经理', description: '聚焦需求、优先级与方案取舍' },
  { value: 'senior_developer', label: '资深开发', description: '聚焦根因、实现与维护成本' },
  { value: 'research_advisor', label: '科研顾问', description: '聚焦证据、方法与结论边界' },
] as const;
export type ResponseStyle = typeof RESPONSE_STYLES[number]['value'];
export type ResponseLength = 'auto' | 'concise' | 'detailed';
export interface ResponsePreferences {
  response_style: ResponseStyle;
  response_length: ResponseLength;
}
export const DEFAULT_RESPONSE_PREFERENCES: ResponsePreferences = {
  response_style: 'general', response_length: 'auto',
};
