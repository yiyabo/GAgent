import React from 'react';
import { Button, Dropdown } from 'antd';
import { GlobalOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';
import { LANGUAGES, useLanguageStore, type Language } from '@/i18n/language';

/** Header control: pick the UI language. The choice is remembered and beats the deployment default. */
const LanguageSwitch: React.FC = () => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const current = LANGUAGES.find((item) => item.code === language) ?? LANGUAGES[0];

  return (
    <Dropdown
      trigger={['click']}
      menu={{
        selectedKeys: [language],
        items: LANGUAGES.map((item) => ({ key: item.code, label: item.label, lang: item.code })),
        onClick: ({ key }) => setLanguage(key as Language),
      }}
    >
      <Button type="text" icon={<GlobalOutlined />} aria-label={t('common.language')} style={{ color: 'var(--text-secondary)' }}>
        {current.label}
      </Button>
    </Dropdown>
  );
};

export default LanguageSwitch;
