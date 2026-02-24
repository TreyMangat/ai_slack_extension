# PRFactory Onboarding (Exact Settings + Invite Steps)

This is the minimal production configuration for a shared, non-hardcoded deployment.

## 1) Required `.env` settings

Set these values:

```env
APP_ENV=prod
APP_DISPLAY_NAME=PRFactory
MOCK_MODE=false
AUTH_MODE=api_token
API_AUTH_TOKEN=<strong-random-token>
BASE_URL=https://<your-modal-url>
ORCHESTRATOR_INTERNAL_URL=https://<your-modal-url>

DATABASE_URL=<neon-or-other-managed-postgres-url>
REDIS_URL=<upstash-or-other-managed-redis-url>
SECRET_KEY=<strong-random-token>
INTEGRATION_WEBHOOK_SECRET=<strong-random-token>

ENABLE_SLACK_BOT=true
SLACK_MODE=http
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_ID=A...
SLACK_APP_CONFIG_TOKEN=xoxe.xoxp-...

GITHUB_ENABLED=true
GITHUB_AUTH_MODE=app
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----
GITHUB_APP_SLUG=<your-github-app-slug>
```

Keep these empty for multi-user portability:

```env
SLACK_ALLOWED_CHANNELS=
SLACK_ALLOWED_USERS=
REVIEWER_ALLOWED_USERS=
GITHUB_REPO_OWNER=
GITHUB_REPO_NAME=
GITHUB_APP_INSTALLATION_ID=
```

## 2) Deploy

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_modal_prod.ps1 -BaseUrl "https://<your-modal-url>"
```

This deploy path automatically:

- syncs Modal secrets
- clears hardcoded Slack/GitHub allowlists/static repo settings
- syncs Slack manifest URLs/events/commands

## 3) Slack app settings (must exist)

Bot scopes:

- `chat:write`
- `commands`
- `channels:read`
- `channels:history`
- `channels:join`
- `groups:read`
- `groups:history`
- `im:read`
- `im:history`
- `mpim:read`
- `mpim:history`

Bot events:

- `member_joined_channel`
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`

Commands:

- `/prfactory`
- `/feature`
- `/prfactory-github`

## 4) How to invite a coworker and use the bot

Same workspace flow:

1. Add coworker to the Slack workspace (normal Slack user invite).
2. In target channel, add the bot:

- `/invite @PRFactory`

3. Coworker runs:

- `/prfactory-github` (connect GitHub app to repo if needed)
- `/prfactory <request>`

4. When asked, coworker provides `repo=org/repo` (required for non-mock builds).

Different workspace:

- Current setup is single-workspace install per app.
- To support external workspaces self-serve, you need Slack OAuth distribution flow (separate feature).
