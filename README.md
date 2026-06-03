# iChat Pro

iChat Pro is a Django-based secure chat project. The repository is organized for team development through Issues, feature branches, and Pull Requests.

> 项目状态：目前仍在开发中，暂未完成。

## Requirements

- Python 3.13+
- Git
- pip

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

The current development settings read `DJANGO_SECRET_KEY` from the environment when it is available. Never commit real secrets or local `.env` files.

## Run Locally

```powershell
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/ in your browser.

## Local Checks

Run the same Django checks used by CI before opening a Pull Request:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## Project Structure

- `ichat_pro/`: project settings and root URL routing
- `accounts/`: login, registration, logout, and future account features
- `chat/`: chat pages and future conversation/message features
- `templates/`: shared Django templates
- `static/`: CSS, JavaScript, and image assets
- `docs/`: product and technical design documents

## Branch Rules

- `main` is the protected integration branch.
- Do not push directly to `main`.
- Create a branch from the latest `main` for every task.
- Use a clear branch name:

```text
feature/<issue-number>-short-name
fix/<issue-number>-short-name
docs/<issue-number>-short-name
```

Example:

```powershell
git switch main
git pull origin main
git switch -c feature/12-chat-message-model
```

## Issue Workflow

Use GitHub Issues to manage work.

1. Create one Issue per task.
2. Assign the Issue to the person who owns the work.
3. Move the work into a feature branch.
4. Link the Issue in the Pull Request with `Closes #<issue-number>`.
5. Merge only after review and required checks pass.

Suggested initial Issues:

- Build account profile model and settings page persistence
- Build chat conversation and message models
- Implement real authentication form validation states
- Add WebSocket room routing and message delivery
- Add tests for accounts and chat views

## Pull Request Rules

- Open PRs into `main`.
- Keep each PR focused on one Issue.
- Include a short summary, linked Issue, screenshots for UI changes, and local test results.
- Request at least one teammate review before merge.
- Prefer squash merge to keep `main` history readable.

## Recommended GitHub Branch Protection

In GitHub, enable branch protection for `main`:

- Require a pull request before merging
- Require approvals before merge
- Require status checks to pass before merge, once CI is configured
- Require branches to be up to date before merging
- Restrict force pushes
- Restrict deletions

## Apps

This project now contains two Django apps:

- `accounts`
- `chat`

## Desktop Client (Electron)

iChat Pro includes a lightweight Electron wrapper for Phase 1 desktop delivery.
The desktop app loads the local Django web client and can either start Django
for you or connect to an already running backend.

### Prerequisites

- Node.js 18+ and npm
- Python virtual environment with the Django dependencies installed

### Quick Start

```powershell
cd desktop
npm install
npm start
```

By default, Electron starts Django with:

```text
python manage.py runserver 127.0.0.1:8000
```

The launcher prefers the project virtual environment when present:

- Windows: `.venv\Scripts\python.exe`
- macOS/Linux: `.venv/bin/python`

### Development Mode

```powershell
cd desktop
npm run dev
```

This opens Chromium DevTools next to the app window.

### Use an Existing Django Server

If Django is already running, skip Electron's backend launcher:

```powershell
$env:ICHAT_SKIP_DJANGO = "1"
cd desktop
npm start
```

### Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `ICHAT_HOST` | `127.0.0.1` | Django host loaded by Electron |
| `ICHAT_PORT` | `8000` | Django port loaded by Electron |
| `ICHAT_PYTHON` | auto-detect | Python executable used to start Django |
| `ICHAT_SKIP_DJANGO` | unset | Set to `1` to connect to an already running backend |

## Demo

Quick demo setup with three pre-configured users:

```powershell
python demo_setup.py
```

Demo accounts after running the script:

| Username | Password |
|----------|----------|
| `alice` | `demo1234` |
| `bob` | `demo1234` |
| `carol` | `demo1234` |

All three are mutual contacts, ready for private and group chat testing.

See [docs/iChat Pro 演示指南.md](docs/iChat%20Pro%20演示指南.md) for a full walkthrough.
