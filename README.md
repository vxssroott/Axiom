# Remix of Axiom Core

Here's a **detailed, structured, and clear prompt** for Lovable to build the complete Axiom application:

---

## 🚀 PROMPT FOR LOVABLE — AXIOM v2.0

### Project Overview

Build a **complete, production-ready single-page application** called **Axiom** — an Engineering Memory Infrastructure platform that helps engineering teams understand their codebases, predict impact of changes, and maintain institutional knowledge.

---

### Core Concept

**Tagline:** *"Most tools optimize writing code. Axiom optimizes understanding codebases."*

Axiom is not a code completion tool. It is an **engineering memory layer** that:

- Maps relationships between files, modules, and dependencies

- Predicts the impact of changes before they're made

- Provides semantic search across codebases

- Maintains a living "Codebase Constitution" with ownership, boundaries, and risk indices

- Uses specialist AI agents for different domains

---

### Technical Requirements

#### Architecture

- **Single HTML file** — Everything in one file (HTML, CSS, JavaScript)

- **No backend required** — All logic runs client-side with localStorage for persistence

- **GitHub OAuth** — For authentication using provided credentials

- **Responsive design** — Desktop-first, mobile-friendly

- **Dark theme** — Enterprise-grade dark UI (not black, but deep dark with purple/blue accents)

#### GitHub OAuth Configuration

```

Client ID: Ov23liyZ0DVT21nm9MWs

Client Secret: be391e21aae2045ee2c34242de545c5981a9573b

Redirect URI: https://vxssroot.github.io/Axiom2.0/

```

---

### Pages & Features

#### 1. Welcome Page

- Clean, minimal landing page

- Logo (⚡) and "Axiom" brand name

- Tagline: "Engineering Memory Infrastructure"

- Mission statement: "Most tools optimize writing code. Axiom optimizes understanding codebases."

- Badges: "Production Ready", "Enterprise Grade", "v2.0.0"

- "Continue →" button that transitions to the dashboard

- No personal branding or "built by" mentions

#### 2. Dashboard (Home)

- **Header:**

  - Logo + version number

  - User profile (avatar + name + username) — visible after login

  - "Sign in with GitHub" button (or "Sign Out" if logged in)

  

- **Stats Grid (4 cards):**

  - Repositories (purple)

  - Files Indexed (blue)

  - Health Score (green) — 0-100%

  - Agents Active (orange) — X/5

- **Engineering Memory Card:**

  - Shows: Files, Modules, Dependencies, Memory Index (%)

  - Subtitle: "Most tools optimize writing code. Axiom optimizes understanding codebases."

- **Quick Actions:**

  - 🕸️ View Graph

  - 📁 Import Repository

  - 🔍 Semantic Search

  - 💥 Impact Prediction

  - 🤖 Activate Agents

#### 3. Knowledge Graph

- Interactive **canvas-based graph visualization** showing nodes and edges

- Nodes represent: Core Modules (purple), Services (blue), Data Layer (green), External Dependencies (orange)

- High-risk nodes highlighted with a red glow and ⚠️ icon

- Click on any node → shows details panel with:

  - Name, Type, Risk Level, Connections count

  - Contextual recommendation based on risk level

- **Legend** showing color codes

- **Node Details** panel (updates on click)

- Graph overlay: "🖱️ Click nodes to explore · 🔄 Drag to navigate"

#### 4. Repositories

- **Import Repository:**

  - Input field (placeholder: "https://github.com/owner/repo")

  - "Import" button

  - Status messages: loading, success, error

  - Simulated import with progress: Cloning → Parsing → Indexing

- **Repository List:**

  - Shows imported repos with:

    - Name (link to GitHub)

    - Description

    - Tags: language, stars, forks, private/public

    - "✓ Indexed" badge when processed

    - "🔍 Search" button to jump to semantic search

#### 5. Semantic Search

- Search input with 🔎 icon

- Suggested quick searches: "authentication flow", "payment validation", "error handling pattern", "database connection"

- Results show:

  - Title

  - Snippet/description

  - File path

  - Similarity score (%)

- Results are simulated but feel realistic

#### 6. Impact Prediction

- Input field: "Enter file path or function name (e.g., src/api/auth.py)"

- "Analyze" button

- Loading state with spinner

- Results show:

  - Risk Score (0-100 with color coding: red >70, orange 40-70, green <40)

  - Affected files count and services count

  - List of impacted files

  - Recommendation (Critical/Medium/Low)

#### 7. Codebase Constitution

- 4 cards showing:

  - Service Boundaries: "X identified"

  - Ownership Map: "X owners mapped"

  - Blast Radius Index: "X high-risk modules"

  - Dark Matter Modules: "X unowned modules"

- Updates based on imported repositories

#### 8. Agent Roster

- 5 agent cards with:

  - Icon and name

  - Role description

  - Status indicator (● Ready / ● Busy)

  - Capabilities (tags)

  - "Activate" button (simulates activation with 2.5s delay)

**Agents:**

1. 🏗️ Architect Agent — Topology & boundary reasoning

2. 🔒 Security Agent — SAST, secrets, supply chain

3. 🐛 Debug Agent — Trace correlation, root cause

4. ⚙️ Infra Agent — Platform operations

5. 🎓 Onboarding Agent — Context for new engineers

#### 9. Sidebar Navigation

- Collapsible on mobile

- Sections: Navigation, System

- Items: Dashboard, Knowledge Graph, Repositories, Semantic Search, Impact Prediction, Constitution, Agents

- System shows: Status (Operational), Memory Index (%)

---

### Authentication Flow

1. User clicks "Sign in with GitHub"

2. Redirect to GitHub OAuth with:

   - client_id

   - redirect_uri

   - scope: repo, user

   - response_type: code

3. After authorization, GitHub redirects back with `?code=`

4. Exchange code for access token using client_secret

5. Fetch user info from GitHub API

6. Fetch user's repositories

7. Save user state in localStorage

8. Update UI to show logged-in state

---

### Design System

#### Colors

```

--bg-primary: #0a0a0f

--bg-secondary: #111118

--bg-card: #181822

--bg-input: #1f1f2e

--border-color: #2a2a3e

--text-primary: #f1f1f7

--text-secondary: #a0a0b8

--text-muted: #5a5a72

--accent-purple: #7c5cfc

--accent-blue: #3b82f6

--accent-green: #22c55e

--accent-red: #ef4444

--accent-orange: #f97316

```

#### Typography

- Font family: 'Inter' (sans-serif), 'JetBrains Mono' (monospace)

- Headings: Bold, tight letter-spacing

- Body: Clean, readable, 1.6 line-height

#### Components

- Cards: Rounded (12px), bordered, with subtle shadows

- Buttons: Primary (purple), secondary (input bg), small (transparent)

- Inputs: Dark bg, purple focus state

- Badges: Rounded, colored borders

---

### Data Persistence

- Use **localStorage** with key: `axiom_state`

- Store: user, repos, filesIndexed, modulesCount, dependenciesCount, healthScore, memoryIndex, graphNodes, graphEdges

- Load state on page load

- Save state on any change

---

### Keyboard Shortcuts

- `Ctrl+K` → Focus Semantic Search

- `Escape` → Blur focused element

---

### Deployment

- **Platform:** GitHub Pages

- **Repo name:** Axiom2.0

- **Live URL:** https://vxssroot.github.io/Axiom2.0/

- **Single file:** index.html

---

### Key Functionality Rules

1. **No mock data** — Everything should feel real and functional

2. **No personal branding** — No names, no "built by", no flags

3. **Enterprise tone** — Serious, professional, infrastructure-grade

4. **Dark theme** — Not black, but deep dark with purple accents

5. **Responsive** — Works on desktop, tablet, and mobile

6. **All-in-one** — Single HTML file, no external dependencies except fonts

---

### Deliverables

- Complete single HTML file (`index.html`)

- All CSS in `<style>` tags

- All JavaScript in `<script>` tags

- Google Fonts included (Inter, JetBrains Mono)

- No external libraries (no React, no Vue, no jQuery)

---

### Acceptance Criteria

- [ ] Welcome page loads with clean, enterprise UI

- [ ] "Continue" button transitions to dashboard

- [ ] GitHub OAuth login works with provided credentials

- [ ] User profile shows after login

- [ ] Repositories import with realistic simulation

- [ ] Knowledge Graph renders nodes and edges

- [ ] Clicking nodes shows details

- [ ] Semantic search returns results

- [ ] Impact prediction shows risk scores

- [ ] Constitution updates based on repos

- [ ] Agents activate with status changes

- [ ] Sidebar navigation works

- [ ] Responsive on all screen sizes

- [ ] No personal branding anywhere

- [ ] localStorage persistence works

- [ ] Keyboard shortcuts work

---

**End of Prompt**

---

This prompt should give Lovable everything needed to build the complete Axiom application. Let me know if you need any adjustments! 🚀

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/dcaf2d92-3328-4b02-9aee-e2577b374c44).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
