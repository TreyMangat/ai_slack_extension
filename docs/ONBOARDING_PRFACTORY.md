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
SLACK_SIGNING_SECRET=...
ENABLE_SLACK_OAUTH=true
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_APP_ID=A...
SLACK_APP_CONFIG_TOKEN=xoxe.xoxp-...
SLACK_APP_CONFIG_REFRESH_TOKEN=...
SLACK_OAUTH_INSTALL_PATH=/api/slack/install
SLACK_OAUTH_CALLBACK_PATH=/api/slack/oauth/callback

GITHUB_ENABLED=true
GITHUB_AUTH_MODE=app
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----
GITHUB_APP_SLUG=<your-github-app-slug>
ENABLE_GITHUB_USER_OAUTH=true
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
GITHUB_USER_OAUTH_REQUIRED=true

# Hosted Repo_Indexer integration (recommended)
INDEXER_BASE_URL=https://<your-repo-indexer-url>
INDEXER_AUTH_TOKEN=<optional-api-token>
INDEXER_REQUIRED=true
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
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_modal_prod.ps1 -BaseUrl "https://<your-modal-url>" -RequireIndexer
```

This deploy path automatically:

- syncs Modal secrets
- clears hardcoded Slack/GitHub allowlists/static repo settings
- syncs Slack manifest URLs/events/commands/oauth-callback

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

- `app_home_opened`
- `member_joined_channel`
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`

Commands:

- `/prfactory`
- `/feature`
- `/prfactory-github`
- `/prfactory-indexer`

## 4) How to invite a coworker and use the bot

Same workspace flow:

1. Add coworker to the Slack workspace (normal Slack user invite).
2. In target channel, add the bot:

- `/invite @PRFactory`

3. Coworker runs:

- `/prfactory <full context request>`

4. When asked, coworker provides `repo=org/repo` (required for non-mock builds).

Different workspace (self-serve install):

1. Share install URL: `https://<your-modal-url>/api/slack/install`
2. Coworker clicks **Add to Slack** and selects their workspace.
3. After install, they invite bot in a channel:
- `/invite @PRFactory`
4. They run:
- `/prfactory <full context request>`

Notes:
- `/prfactory-github` still works, but PRFactory now also posts user-specific GitHub connect links in onboarding and intake prompts.
- This flow supports multiple workspaces because each install stores a workspace-specific bot token.
- Each Slack user connects their own GitHub account for builds (no shared GitHub identity in-channel).
- `/prfactory` command text is treated as full prompt context; PRFactory asks for a short title as the first thread reply.

## 5) App ID, Team ID, and install links

- App ID: Slack app settings -> **Basic Information** -> **App Credentials** (`SLACK_APP_ID`).
- Team ID (for a specific workspace): run `auth.test` with that workspace bot token, or inspect Slack workspace metadata.

Link options:
- One-size-fits-all cross-workspace install link: `https://<your-modal-url>/api/slack/install`
- Workspace-targeted app redirect link: `https://slack.com/app_redirect?app=<SLACK_APP_ID>&team=<TEAM_ID>`
