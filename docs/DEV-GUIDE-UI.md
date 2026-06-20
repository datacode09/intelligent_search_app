# Development & Testing Guide — UI Component (`hydro-one-hods-ui`)

## Overview

The UI is a React 18 SPA built with Vite. It provides a search form, debounced query optimization, multi-select content type filtering, and document result display with extractive caption highlights. Authentication via MSAL (Microsoft Authentication Library) is defined as a TODO and must be implemented before the app works against a production API.

**Technology:** React 18, Vite 6, `@azure/msal-browser`, `@azure/msal-react` (to be added)

---

## Local Development Setup

### 1. Prerequisites

- Node.js 20+
- npm 10+

### 2. Install dependencies

```bash
cd hydro-one-hods-ui
npm install
```

### 3. Configure environment variables

Create a `.env.local` file (gitignored):

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_CLIENT_ID=<entra-app-registration-client-id>
VITE_TENANT_ID=<your-azure-tenant-id>
```

- `VITE_API_BASE_URL` — points to the local API during development; leave empty to use the Vite proxy
- `VITE_CLIENT_ID` / `VITE_TENANT_ID` — required once MSAL is integrated (ISSUE-5)

### 4. Run the dev server

```bash
npm run dev
```

The app is available at `http://localhost:5173`.

> **Current limitation:** Without MSAL (ISSUE-5 not yet fixed), all API calls return HTTP 401 from a real API. For local UI development without auth, either:
> - Run the API locally with auth disabled (comment out `Depends(require_auth)` in `main.py` temporarily), or
> - Point `VITE_API_BASE_URL` at a mock server (see Mock API section below).

### 5. Build for production

```bash
npm run build    # outputs to dist/
npm run preview  # serve the built output locally
```

---

## Project Structure

```
hydro-one-hods-ui/
├── src/
│   ├── App.jsx          # Main component: search logic, filter UI, results display
│   ├── main.jsx         # React root mount (MsalProvider goes here — ISSUE-5)
│   └── index.css        # All styles
├── public/
│   └── data.json        # Static mock data (for offline testing)
├── index.html
├── vite.config.js
└── package.json
```

### Key areas in `App.jsx`

| Section | Lines | Purpose |
|---|---|---|
| `CONTENT_TYPE_OPTIONS` | ~56–77 | Hardcoded content type list (replace with facet fetch — ISSUE-8) |
| `useEffect` (optimizer) | ~240–295 | Debounced query optimizer: 1.1s delay, AbortController, 5s kill timer |
| `runSearch()` | ~300–324 | Calls `POST /query`; sets results state |
| `MultiSelectDropdown` | ~131–203 | Reusable multi-select component with chip tags |
| Result rendering | ~419–478 | Maps results to cards with highlights, badges, and document links |

---

## MSAL Integration (ISSUE-5 — Required Before Production)

MSAL is not yet integrated. All API calls currently send no `Authorization` header and will return HTTP 401 in production. Follow these steps to implement it:

### Step 1 — Install packages

```bash
npm install @azure/msal-browser @azure/msal-react
```

### Step 2 — Create `src/authConfig.js`

```js
export const msalConfig = {
  auth: {
    clientId: import.meta.env.VITE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_TENANT_ID}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'sessionStorage',
    storeAuthStateInCookie: false,
  },
}

export const loginRequest = {
  scopes: [`api://${import.meta.env.VITE_CLIENT_ID}/user_impersonation`],
}
```

### Step 3 — Wrap the app in `src/main.jsx`

```jsx
import { PublicClientApplication } from '@azure/msal-browser'
import { MsalProvider } from '@azure/msal-react'
import { msalConfig } from './authConfig'

const msalInstance = new PublicClientApplication(msalConfig)

ReactDOM.createRoot(document.getElementById('root')).render(
  <MsalProvider instance={msalInstance}>
    <App />
  </MsalProvider>
)
```

### Step 4 — Acquire a token before each API call in `App.jsx`

```jsx
import { useMsal } from '@azure/msal-react'
import { loginRequest } from './authConfig'

// Inside the App component:
const { instance, accounts } = useMsal()

const getToken = async () => {
  const response = await instance.acquireTokenSilent({
    ...loginRequest,
    account: accounts[0],
  })
  return response.accessToken
}

// In runSearch and the optimizer fetch, add:
const token = await getToken()
// headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }
```

---

## Mock API for Local UI Development

To develop the UI without a running backend, serve `public/data.json` as a local mock:

```bash
# Install json-server globally
npm install -g json-server

# Create a mock routes file
cat > mock-server.json << 'EOF'
{
  "query": { "count": 2, "answers": [], "results": [
    { "DocumentName": "HO-Safety-001.pdf", "DocumentUrl": "#", "Prefix": "HO", "ContentType": ["Safety Equipment and Practices"], "Highlights": ["This document covers <em>safety</em> equipment requirements."] },
    { "DocumentName": "AL-Standards-002.docx", "DocumentUrl": "#", "Prefix": "AL", "ContentType": ["Corporate Standards"], "Highlights": ["Applicable <em>corporate</em> standards for AL region."] }
  ]},
  "OptimizeHybridQueries": { "OptimizedQuery": "safety equipment grounding", "keywords": ["safety", "equipment", "grounding"] }
}
EOF

json-server --watch mock-server.json --port 8000 --routes routes.json
```

Set `VITE_API_BASE_URL=http://localhost:8000` in `.env.local`.

---

## Running Tests

> **No tests exist yet** (task 52 in the tracker). The test runner is not configured. Below is the recommended setup.

### Add Vitest

```bash
npm install -D vitest @testing-library/react @testing-library/user-event @testing-library/jest-dom jsdom
```

Add to `vite.config.js`:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: true,
  },
})
```

Create `src/test/setup.js`:

```js
import '@testing-library/jest-dom'
```

Add to `package.json`:

```json
"scripts": {
  "test": "vitest",
  "test:coverage": "vitest --coverage"
}
```

### Recommended test cases to implement

| Test | What to assert |
|---|---|
| Search form submit | Fires `POST /query` with the correct body |
| Optimizer debounce | Does not fire before 1.1s; fires once after pause |
| Optimizer failure | Shows soft warning; search input remains enabled (ISSUE-9) |
| Multi-select dropdown | Toggling options updates the chip list |
| Result rendering | Document title renders; link shown only when URL is not `"#"` |
| Highlight rendering | `<em>` tags in captions are replaced with `<mark class="hl">` |
| MSAL token injection | `Authorization: Bearer <token>` header present on all API calls |

---

## Linting

ESLint is configured via `eslint.config.js`:

```bash
npx eslint src/
npx eslint src/ --fix
```

---

## Deployment

Deployed via `azure-pipelines/ui.yml` to Azure Static Web Apps. Manual deployment:

```bash
npm run build
npx @azure/static-web-apps-cli deploy ./dist \
  --deployment-token <token-from-portal> \
  --env production
```

After deployment, navigate to the Static Web App URL and confirm the search form renders. Open browser DevTools → Network and verify `Authorization: Bearer <token>` is present on requests to `/query`.
