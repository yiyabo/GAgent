# i18n

Two UI languages, English (`en`) and Chinese (`zh-CN`). Catalogues: `locales/en.json` and `locales/zh-CN.json`.

## Which language does a visitor get?

1. **Their own choice** (header switch, stored as `gagent.language`) always wins.
2. Otherwise the **deployment default**, set when the front end is built:

   ```bash
   VITE_DEFAULT_LANGUAGE=en    npm run build   # CityU (620b)
   VITE_DEFAULT_LANGUAGE=zh-CN npm run build   # .8
   ```

   Unset or unknown means English. It also sets `<html lang>`, the page title and the meta description in
   `index.html` before React starts (`htmlLanguage` in `vite.config.ts`).

## Writing UI text

```tsx
const t = useT();            // from '@/i18n/useT'
<Button>{t('common.save')}</Button>
message.success(t('header.passwordUpdated'));
```

* Keys are typed against `en.json`: a typo is a compile error, not a raw key on screen.
* Add the key to **both** catalogues. Keep `{{placeholders}}` identical in both.
* Outside React use `translate('group.key')` from the same file.
* Never write visible text directly in a component. `i18n.test.ts` counts hard-coded text per file
  (`hardcodedBaseline.ts`) and fails if a file gains any. When you migrate a file, delete its line from the baseline.

## What the tests enforce

* both catalogues have the same keys, no empty values, the same placeholders, no untranslated copies;
* a stored choice beats the deployment default; a bad stored value is ignored;
* the header, `<html lang>`, the title and antd's own strings (pager, empty states) all follow the language;
* untranslated text may only shrink, file by file.

antd and dayjs locales follow the language in `theme/ThemeProvider.tsx`.
