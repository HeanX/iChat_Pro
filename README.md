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
