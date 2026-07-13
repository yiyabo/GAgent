module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: [
    'dist',
    '.eslintrc.cjs',
    'e2e',
    'node_modules',
    'vite.config.ts',
    'vitest.config.ts',
    'playwright.config.ts',
  ],
  parser: '@typescript-eslint/parser',
  plugins: ['react-refresh'],
  rules: {
    'react-refresh/only-export-components': 'off',
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-unused-vars': 'off',
    '@typescript-eslint/ban-ts-comment': 'off',
    'no-console': 'off',
    'no-empty': 'off',
    '@typescript-eslint/no-empty-function': 'off',
    'prefer-const': 'off',
    'no-case-declarations': 'off',
    'no-useless-escape': 'off',
    'no-extra-boolean-cast': 'off',
    'no-prototype-builtins': 'off',
    'react-hooks/exhaustive-deps': 'off',
  },
  overrides: [
    {
      files: ['src/components/terminal/TerminalPanel.tsx'],
      rules: {
        'no-await-in-loop': 'warn',
      },
    },
    {
      files: ['src/components/layout/ExecutorPanel.tsx'],
      rules: {
        'react-hooks/exhaustive-deps': 'warn',
      },
    },
  ],
};
