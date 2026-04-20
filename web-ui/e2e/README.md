# Frontend E2E Tests

Browser-driven end-to-end tests using [Playwright](https://playwright.dev/) that cover the core user interaction paths: login, chat, and plan viewing.

## Prerequisites

1. **Install dependencies** (includes `@playwright/test`):

   ```bash
   cd web-ui
   npm ci
   ```

2. **Install Playwright browsers**:

   ```bash
   npx playwright install --with-deps chromium
   ```

3. **Start the backend** (in a separate terminal):

   ```bash
   # From the project root
   conda activate LLM
   python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 9000
   ```

4. **Start the frontend dev server** (in a separate terminal):

   ```bash
   cd web-ui
   npm run dev
   ```

## Running Tests

```bash
# Run all E2E tests
npm run test:e2e

# Or directly via Playwright
npx playwright test

# Run a specific test file
npx playwright test login.spec.ts

# Run in headed mode (see the browser)
npx playwright test --headed

# Run with Playwright UI
npx playwright test --ui

# List all tests without running
npx playwright test --list
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:3001` | Frontend dev server URL |
| `API_BASE_URL` | `http://localhost:9000` | Backend API URL |
| `E2E_LLM_MODE` | `real` | `mock` or `real` — controls whether the backend uses real or mocked LLM responses |

Example with overrides:

```bash
BASE_URL=http://localhost:5173 API_BASE_URL=http://localhost:8000 npx playwright test
```

## Page Object Models

Test specs use page objects to encapsulate UI selectors and interactions. This keeps tests resilient to UI changes — when a selector changes, only the page object needs updating.

| Page Object | File | Responsibility |
|-------------|------|----------------|
| `LoginPage` | `pages/LoginPage.ts` | Login form: email, password, submit, error alert |
| `ChatPage` | `pages/ChatPage.ts` | Chat panel: message input, send, message list, response wait |
| `PlansPage` | `pages/PlansPage.ts` | Plans: plan selector dropdown, plan detail, DAG visualization |

## Directory Structure

```
web-ui/e2e/
├── pages/
│   ├── LoginPage.ts      # Login page object
│   ├── ChatPage.ts       # Chat page object
│   └── PlansPage.ts      # Plans page object
├── login.spec.ts          # Login flow tests
├── chat.spec.ts           # Chat interaction tests
├── plans.spec.ts          # Plan viewing tests
└── README.md              # This file
```

## CI

Frontend E2E tests run nightly via `.github/workflows/nightly-e2e.yml` with `E2E_LLM_MODE=mock` so no LLM API keys are required. On failure, Playwright trace files are uploaded as build artifacts.
