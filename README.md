# Autonomous GitHub Pull Request Repair Agent

A production-grade Python application designed for continuous, unattended monitoring of GitHub pull requests. When review feedback (e.g. `REQUEST_CHANGES` or code review comments) or CI workflow checks fail, the application automatically orchestrates an AI coding agent (such as **Antigravity**) to diagnose problems, perform surgical code modifications in isolated workspaces, execute local verification test suites, validate strict git diff safety constraints, and push verified fixes back to the PR branch.

---

## 1. Core Workflow

```text
               +----------------------------------------+
               | Background Service (APScheduler)       |
               +-------------------+--------------------+
                                   | Every N hours / Scan Now
                                   v
               +----------------------------------------+
               | GitHub Discovery & Event Deduplication  |
               +-------------------+--------------------+
                                   | Action Required?
                 +-----------------+-----------------+
                 | No                                | Yes
                 v                                   v
         +---------------+         +------------------------------------+
         |   Sleep       |         | Concurrency Lock & Attempt Limits  |
         +---------------+         +-----------------+------------------+
                                                     |
                                                     v
                                   +------------------------------------+
                                   | Isolated Workspace & PR Context    |
                                   +-----------------+------------------+
                                                     |
                                                     v
                                   +------------------------------------+
                                   | Antigravity AI Coding Agent        |
                                   +-----------------+------------------+
                                                     |
                                                     v
                                   +------------------------------------+
                                   | Verification Suites (Test/Lint)    |
                                   +-----------------+------------------+
                                                     | Pass
                                                     v
                                   +------------------------------------+
                                   | Git Diff Safety & Secret Scanner   |
                                   +-----------------+------------------+
                                                     | Safe
                                                     v
                                   +------------------------------------+
                                   | Auto Push or Human Approval Mode   |
                                   +------------------------------------+
```

1. **Continuously monitors** configured GitHub repositories on a periodic schedule (e.g., every 3 hours) or manual trigger.
2. **Detects actionable events**:
   - New code review comments (inline line comments).
   - Reviews requesting changes (`CHANGES_REQUESTED`).
   - PR discussion comments.
   - Failed GitHub Actions workflow jobs and steps.
   - Failed check runs and commit statuses.
3. **Prevents duplicate processing**: Stores all event IDs in a SQLite database; identical review comments or check runs are never re-processed.
4. **Collects PR context**: Pulls PR description, diffs, comments, CI failure summaries, and repository-specific instruction files (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `README.md`) into a structured `pr-context.md`.
5. **Drives Coding Agent**: Provides a surgical repair prompt to Antigravity instructing it to fix only the reported problems while preserving codebase architecture.
6. **Executes verification**: Runs repository-defined test, lint, static analysis, and security checks.
7. **Inspects git diff safety**: Verifies change size thresholds (max files, max lines added/deleted), blocks forbidden files (`.env`, `credentials.json`, `*.pem`, `id_rsa`), and scans for leaked secrets.
8. **Enforces conservative push guardrails**:
   - Strictly pushes **only** to the origin PR branch (`git push origin <PR_BRANCH>`).
   - **Never** force-pushes (`--force` is strictly prohibited).
   - **Never** pushes to protected branches (`main`, `master`, `develop`).
   - **Never** automatically merges or closes a pull request.
9. **Caps repair attempts**: Stops automatically after a configurable threshold (default 3 attempts) and sends alerts.

---

## 2. Architecture

```text
github-pr-repair-agent/
│
├── app/
│   ├── config/
│   │   ├── settings.py          # Pydantic v2 settings loading from .env
│   │   └── repositories.py      # YAML repository parser & validator
│   │
│   ├── github/
│   │   ├── client.py            # Async httpx client with rate-limiting
│   │   ├── prs.py               # PR discovery, details, file patches, diffs
│   │   ├── reviews.py           # Review detection (CHANGES_REQUESTED)
│   │   ├── comments.py          # Inline code comments & PR comments
│   │   ├── checks.py            # Check runs and commit statuses
│   │   └── actions.py           # Actions workflow runs & intelligent CI log parser
│   │
│   ├── agent/
│   │   ├── base.py              # CodingAgent interface & RepairResult contract
│   │   ├── antigravity.py       # Antigravity agent adapter (agentapi CLI / SDK)
│   │   ├── mock.py              # Mock agent for deterministic testing
│   │   ├── context.py           # PR Context builder (pr-context.md)
│   │   └── prompts.py           # Surgical PR repair prompt templates
│   │
│   ├── workspace/
│   │   ├── manager.py           # Isolated workspace lifecycle (workspaces/<repo>/pr-<n>)
│   │   ├── git.py               # Safe Git subprocess runner & push guardrails
│   │   └── cleanup.py           # Workspace retention & cleanup policy
│   │
│   ├── verification/
│   │   ├── runner.py            # Test, lint, and static analysis runner
│   │   └── diff.py              # Git diff safety inspector & secret scanner
│   │
│   ├── monitoring/
│   │   ├── scanner.py           # Main PR repair loop & event deduplicator
│   │   ├── analyzer.py          # Review comments grouping & diagnostic synthesizer
│   │   └── state.py             # Explicit state machine & concurrency locks
│   │
│   ├── scheduler/
│   │   └── scheduler.py         # APScheduler background runner
│   │
│   ├── notifications/
│   │   ├── base.py              # NotificationProvider interface
│   │   ├── console.py           # Rich terminal cards
│   │   ├── slack.py             # Slack Incoming Webhooks
│   │   ├── email.py             # SMTP email notifications
│   │   └── manager.py           # Notification dispatcher & database logger
│   │
│   ├── database/
│   │   ├── models.py            # SQLAlchemy models (PRs, Events, Repairs, Commits)
│   │   └── database.py          # Async & sync engine and session management
│   │
│   ├── api/
│   │   └── routes.py            # REST endpoints for dashboard & actions
│   │
│   ├── cli.py                   # Click CLI (`pr-agent start|scan|repair|status|doctor`)
│   └── main.py                  # FastAPI server entrypoint
│
├── dashboard/
│   └── index.html               # Modern single-page web dashboard (React + Tailwind)
│
├── config/
│   └── repositories.yaml        # Monitored repository specifications & check commands
│
├── tests/                       # 23 automated tests (unit, safety, and e2e)
├── workspaces/                  # Isolated repair workspace directories
├── logs/                        # Runtime and agent execution logs
├── data/                        # SQLite database storage
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## 3. Requirements

- **Python**: 3.10 or higher (tested and verified on Python 3.14).
- **Git**: 2.30+ installed and available in `PATH`.
- **Operating System**: macOS or Linux.
- **Docker** (Optional): for containerized deployment.
- **Antigravity**: `agentapi` CLI tool or Python SDK installed in the environment.

---

## 4. GitHub Authentication

1. Create a GitHub Personal Access Token (Classic or Fine-Grained) with:
   - `repo` (Full control of private repositories, or public repo access)
   - `workflow` (Optional, to inspect Actions workflows)
2. Add token to your `.env` file:
   ```bash
   GITHUB_TOKEN=ghp_your_github_token_here
   GITHUB_USERNAME=your-username
   ```

---

## 5. Antigravity Setup & Adapter

The application interfaces with Antigravity through a pluggable `CodingAgent` abstraction:

```python
class CodingAgent:
    async def repair(self, context: AgentContext) -> RepairResult:
        raise NotImplementedError
```

The implemented `AntigravityAgent` supports:
- Invoking the Antigravity `agentapi` CLI (`agentapi new-conversation --model=<model> "<prompt>"`) directly inside the isolated workspace.
- Configurable models: `flash_lite`, `flash`, or `pro` (default: `flash`).
- Strict timeout management (default: 900 seconds).
- Structured output parsing conforming to the agent completion contract:
  ```json
  {
    "status": "success",
    "summary": "Fixed course enrollment validation and updated unit tests.",
    "files_changed": ["includes/class-api.php", "tests/test-api.php"],
    "tests_run": ["pytest"],
    "tests_passed": true,
    "notes": ""
  }
  ```

---

## 6. Configuration

### `.env` File
Copy `.env.example` to `.env` and set your credentials:
```bash
cp .env.example .env
```

Key environment variables:
| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | *None* | GitHub Personal Access Token |
| `GITHUB_USERNAME` | *None* | Your GitHub username (filters PRs if enabled) |
| `SCHEDULER_INTERVAL_HOURS` | `3` | Periodic scan frequency in hours |
| `AUTOMATION_MODE` | `auto` | `auto` (auto commit & push) or `approval` (waits for review) |
| `AGENT_PROVIDER` | `antigravity` | Agent to use (`antigravity` or `mock`) |
| `ANTIGRAVITY_MODEL` | `flash` | Antigravity model: `flash_lite`, `flash`, `pro` |
| `SAFETY_MAX_FILES_CHANGED`| `20` | Max files changed in repair diff |
| `SAFETY_MAX_LINES_ADDED` | `1000` | Max lines added in repair diff |
| `REPAIR_MAX_ATTEMPTS` | `3` | Max repair attempts per PR |
| `SLACK_WEBHOOK_URL` | *None* | Slack Incoming Webhook URL for alerts |

### `config/repositories.yaml`
Define the repositories to monitor and their specific test/lint check commands:
```yaml
repositories:
  - name: my-project
    github: my-org/my-project
    enabled: true
    pull_requests:
      only_my_prs: true
      only_open_prs: true
    checks:
      test:
        - "pytest tests/"
      lint:
        - "flake8 src/"
      static_analysis:
        - "mypy src/"
    safety:
      max_files_changed: 15
      max_lines_added: 500
      max_lines_deleted: 500
      allow_new_files: true
      allow_deleted_files: false
    repair:
      max_attempts: 3
```

---

## 7. Running Locally

### Installation
```bash
git clone https://github.com/your-org/github-pr-repair-agent.git
cd github-pr-repair-agent

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install --upgrade pip
pip install -e ".[dev]"
```

### Run System Health Check
```bash
pr-agent doctor
```

Output:
```text
Running PR Repair Agent Doctor Diagnostics...

  ✔ Git installed: git version 2.50.1
  ✔ GITHUB_TOKEN configured: ghp_...
  ✔ Antigravity binary available at: /Users/rashed/.gemini/antigravity/bin/agentapi
  ✔ Configuration loaded: 1 repository/ies defined in repositories.yaml
  ✔ SQLite Database initialized at: sqlite:///./data/pr_agent.db

Diagnostics complete.
```

### Start the Agent & Web Dashboard
```bash
pr-agent start
```
The FastAPI server and React dashboard will be available at: **`http://localhost:8000`**.

---

## 8. Running with Docker

Use Docker Compose for containerized, background operation with persistent volumes for data, workspaces, and logs:

```bash
docker-compose up -d --build
```

Inspect container status and logs:
```bash
docker-compose logs -f pr-agent
```

---

## 9. Safety Model & Push Guardrails

The application was built with a strict conservative safety model.

### Absolute Invariants:
1. **Never Merges a PR**: Automatic merging is strictly prohibited.
2. **Never Closes a PR**: PR lifecycle closure requires human action.
3. **Never Force-Pushes**: Git push explicitly forbids `--force`.
4. **Never Touches Protected Branches**: Operations targeting `main`, `master`, `develop`, `release`, `staging`, `production` are aborted immediately.
5. **No Secret Leaks**: Before commit or push, diffs are scanned for AWS keys, GitHub tokens, Slack tokens, and private keys.
6. **Forbidden Files**: `.env`, credentials, `*.pem`, `id_rsa`, and SSH keys cannot be added or committed.
7. **Threshold Controls**: Diffs that touch more than `max_files_changed` or add more than `max_lines_added` are held for human review (`REQUIRES_HUMAN_REVIEW`).

---

## 10. Approval Mode vs. Auto Mode

### Auto Mode (`AUTOMATION_MODE=auto`)
1. Agent diagnoses and fixes code.
2. Verification tests pass.
3. Diff safety checks pass.
4. Agent commits changes and pushes directly to the existing PR branch.

### Approval Mode (`AUTOMATION_MODE=approval`)
1. Agent diagnoses and fixes code.
2. Verification tests pass.
3. PR transitions to **`REQUIRES_HUMAN_REVIEW`**.
4. The verified diff is rendered on the Web Dashboard.
5. Pushing is paused until a human clicks **"Approve & Push Fix"** in the Dashboard or calls `/api/pull-requests/{id}/approve`.

---

## 11. CLI Commands Reference

| Command | Usage | Description |
|---|---|---|
| `pr-agent connect` | `pr-agent connect` | Interactively configure and test GitHub & Antigravity connections |
| `pr-agent doctor` | `pr-agent doctor` | Runs diagnostic health check on environment |
| `pr-agent start` | `pr-agent start [--host 0.0.0.0] [--port 8000]` | Starts service and web dashboard |
| `pr-agent scan` | `pr-agent scan [--repo owner/repo] [--pr 123]` | Triggers manual scan across repositories |
| `pr-agent repair` | `pr-agent repair --repo owner/repo --pr 123` | Forces repair pipeline for a specific PR |
| `pr-agent status` | `pr-agent status` | Displays tabular status of monitored PRs |
| `pr-agent logs` | `pr-agent logs [--limit 10]` | Shows recent repair attempt logs and outcomes |

---

## 12. REST API Reference

- `GET /api/health` - Health check and system mode.
- `GET /api/status` - Aggregated metric counters for dashboard.
- `GET /api/settings/integrations` - Get GitHub & Antigravity connection status.
- `POST /api/settings/integrations` - Save GitHub & Antigravity configuration.
- `POST /api/settings/test-github` - Test GitHub authentication.
- `POST /api/settings/test-antigravity` - Test Antigravity binary availability.
- `GET /api/repositories` - List configured repositories.
- `GET /api/pull-requests` - List tracked PRs (filterable by `status` and `repo`).
- `GET /api/pull-requests/{id}` - Complete details, status, and latest diff.
- `GET /api/pull-requests/{id}/repairs` - Full repair attempt history.
- `POST /api/scan` - Trigger an immediate scan across all repositories.
- `POST /api/pull-requests/{id}/repair` - Queue repair for a PR.
- `POST /api/pull-requests/{id}/approve` - Approve verified changes and push to branch.
- `POST /api/pull-requests/{id}/stop` - Halt automated repairs on a PR.

---

## 13. Running Automated Tests

Run the full pytest test suite:
```bash
pytest -v tests/
```

Test coverage includes:
- GitHub PR discovery, review detection, and CI log extraction.
- Agent abstraction, prompt generation, and context generation.
- Push guardrails, protected branch rejection, and force-push blocking.
- Diff safety thresholds, forbidden files (`.env`), and secret scanning.
- Multi-language verification runner and command timeout handling.
- State transitions, concurrency locking, and attempt limit enforcement.
- End-to-end simulated acceptance test demonstrating full repair lifecycle.

---

## 14. License

Released under the [MIT License](LICENSE).
