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

## External-instance mode (recommended)

When `BASE_URL` is set, the config skips starting the local dev server and
tests run against any already-running instance — e.g. an isolated backend
serving the built SPA:

```bash
# On the server: boot an isolated instance (own empty DB, mocked LLM, no rate limits)
LLM_MOCK=true RATE_LIMIT_LOGIN=100000 RATE_LIMIT_REGISTER=100000 \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 9126 --no-access-log

# Register the test user the specs expect (one time per fresh DB)
curl -X POST http://127.0.0.1:9126/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@example.com","password":"password123"}'

# Locally: tunnel if needed, then run
ssh -N -L 9200:127.0.0.1:9126 user@server &
BASE_URL=http://localhost:9200 API_BASE_URL=http://localhost:9200 npx playwright test
```

Notes:

- `LLM_MOCK=true` makes the LLM health ping instant; in real mode it does a
  live ~10 s LLM call per page load, which can gate first paint.
- The suite is configured with `workers: 1` because it shares one backend
  instance (login rate limits, per-session state).

## Page Object Models

Test specs use page objects to encapsulate UI selectors and interactions. This keeps tests resilient to UI changes — when a selector changes, only the page object needs updating.

| Page Object | File | Responsibility |
|-------------|------|----------------|
| `LoginPage` | `pages/LoginPage.ts` | Login form: email, password, submit, error alert |
| `ChatPage` | `pages/ChatPage.ts` | Chat panel: message input, send, message list, response wait |
| `PlansPage` | `pages/PlansPage.ts` | Plan side-panel on /chat: tab list, tab switching, active panel |

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

Unit tests run in GitHub Actions on every push (`.github/workflows/ci.yml`).
E2E currently runs on demand against an isolated instance (see above);
wiring it into CI requires booting the backend in the workflow and is
tracked as follow-up work.
