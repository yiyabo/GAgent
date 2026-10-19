import React, { useEffect, useRef, useId, useState } from 'react';
import { Button, Checkbox, Popover, Radio, Segmented, message } from 'antd';
import { DownOutlined, CheckOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useAuthStore } from '@store/auth';
import { useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';
import { DEFAULT_RESPONSE_PREFERENCES, RESPONSE_STYLES } from '@/types/responseStyle';
import type { ResponseLength, ResponsePreferences } from '@/types/responseStyle';
import './ResponseStyleSelector.css';

const LENGTHS: Array<{ label: TranslationKey; value: ResponseLength }> = [
  { label: 'responseStyle.lengthAuto', value: 'auto' },
  { label: 'responseStyle.lengthConcise', value: 'concise' },
  { label: 'responseStyle.lengthDetailed', value: 'detailed' },
];

const ResponseStyleSelector: React.FC = () => {
  const t = useT();
  const session = useChatStore((state) => state.currentSession);
  const defaults = useChatStore((state) => state.responseDefaults);
  const saving = useChatStore((state) => state.responsePreferenceSaving.has(session?.id ?? ''));
  const authenticated = useAuthStore((state) => state.authenticated);
  const user = useAuthStore((state) => state.user);
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const groupName = useId();
  const [draft, setDraft] = useState<ResponsePreferences>(DEFAULT_RESPONSE_PREFERENCES);
  const [saveDefault, setSaveDefault] = useState(false);
  useEffect(() => {
    if (open) panelRef.current?.querySelector<HTMLInputElement>('input:checked')?.focus();
  }, [open]);
  // Existing chats without a saved preference keep the original general style.
  const effective = {
    response_style: session?.settings?.response_style ?? (session?.titleSource === 'local' ? defaults?.response_style : undefined) ?? 'general',
    response_length: session?.settings?.response_length ?? (session?.titleSource === 'local' ? defaults?.response_length : undefined) ?? 'auto',
  } as ResponsePreferences;
  useEffect(() => { void useChatStore.getState().loadResponseDefaults(); }, [user]);
  useEffect(() => { setOpen(false); }, [session?.id, user]);
  const style = RESPONSE_STYLES.find((entry) => entry.value === effective.response_style) ?? RESPONSE_STYLES[0];
  const lengthKey = LENGTHS.find((entry) => entry.value === effective.response_length)?.label;
  const length = lengthKey ? t(lengthKey) : undefined;
  const lengthOptions = LENGTHS.map((entry) => ({ label: t(entry.label), value: entry.value }));
  const styleLabel = t(style.label);

  const apply = async () => {
    const target = session ?? useChatStore.getState().startNewSession();
    try {
      await useChatStore.getState().updateResponsePreferences(target.id, draft, saveDefault);
      setOpen(false);
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('responseStyle.saveFailed'));
    }
  };
  return (
    <Popover classNames={{ root: 'response-style-popover' }} trigger="click" placement="topLeft" open={open} onOpenChange={(next) => {
      if (next) { setDraft(effective); setSaveDefault(false); }
      setOpen(next);
    }} content={
      <div ref={panelRef} className="response-style-panel" role="dialog" aria-label={t('responseStyle.dialogLabel')} onKeyDown={(event) => {
        if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); triggerRef.current?.focus(); }
      }}>
        <div className="response-style-heading">{t('responseStyle.heading')}</div>
        <Radio.Group name={groupName} aria-label={t('responseStyle.heading')} value={draft.response_style} className="response-style-options" onChange={(event) => setDraft({ ...draft, response_style: event.target.value })}>
          {RESPONSE_STYLES.map((entry) => (
            <Radio key={entry.value} value={entry.value} className="response-style-option">
              <span className="response-style-name">{t(entry.label)}</span>
              <span className="response-style-description">{t(entry.description)}</span>
            </Radio>
          ))}
        </Radio.Group>
        <div className="response-style-heading response-style-length">{t('responseStyle.lengthHeading')}</div>
        <Segmented block options={lengthOptions} value={draft.response_length} onChange={(value) => setDraft({ ...draft, response_length: value as ResponseLength })} />
        <p className="response-style-hint">{t('responseStyle.hint')}</p>
        {authenticated && <Checkbox checked={saveDefault} onChange={(event) => setSaveDefault(event.target.checked)}>{t('responseStyle.saveDefault')}</Checkbox>}
        <div className="response-style-actions">
          <Button size="small" onClick={() => { setOpen(false); triggerRef.current?.focus(); }}>{t('common.cancel')}</Button>
          <Button size="small" type="primary" loading={saving} icon={<CheckOutlined />} onClick={() => void apply()}>{t('responseStyle.apply')}</Button>
        </div>
      </div>
    }>
      <Button ref={triggerRef} type="text" size="small" className="response-style-trigger" aria-label={t('responseStyle.triggerLabel', { style: styleLabel, length })} aria-expanded={open}>
        <span className="response-style-trigger-prefix">{t('responseStyle.heading')}</span>{styleLabel}{effective.response_length !== 'auto' && <span> · {length}</span>}<DownOutlined />
      </Button>
    </Popover>
  );
};
export default ResponseStyleSelector;
