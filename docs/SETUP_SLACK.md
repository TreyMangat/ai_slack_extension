# Slack Setup (Socket or HTTP Mode)

PRFactory uses Slack Bolt (Python) with:
- `SLACK_MODE=socket` for local/dev bot process
- `SLACK_MODE=http` for cloud/Modal (`/api/slack/events`, recommended)

Primary user command:
- `/prfactory <request>`

Compatibility/utility commands:
- `/feature <request>` (legacy alias)
- `/prfactory-github` (GitHub account connect link for that Slack user)

## 1) Create Slack app + scopes

1. Create a Slack app.
2. Add bot OAuth scopes:
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
3. Add bot events:
- `app_home_opened`
- `member_joined_channel`
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`
4. Add slash commands:
- `/prfactory`
- `/feature`
- `/prfactory-github`

## 2) Configure `.env`

Required:
- `ENABLE_SLACK_BOT=true`
- `SLACK_MODE=http` (Modal/cloud)
- `SLACK_SIGNING_SECRET=...`
- `SLACK_APP_ID=A...`
- `SLACK_APP_CONFIG_TOKEN=xoxe.xoxp-...` (App Configuration Token from `https://api.slack.com/apps`)
- `ENABLE_SLACK_OAUTH=true`
- `SLACK_CLIENT_ID=...`
- `SLACK_CLIENT_SECRET=...`
  - both are in Slack app settings -> **Basic Information** -> **App Credentials**

Optional:
- `SLACK_BOT_TOKEN=xoxb-...` (single-workspace fallback token)
- `SLACK_APP_TOKEN=xapp-...` (only for socket mode)
- `REVIEWER_ALLOWED_USERS=...`
- `SLACK_ALLOWED_CHANNELS=...`
- `SLACK_ALLOWED_USERS=...`

## 3) Remove manual URL/event edits

Run:

```powershell
py -3.12 .\scripts\sync_slack_manifest.py --env-file .env
```

This updates Slack manifest settings automatically:
- Events request URL -> `<BASE_URL>/api/slack/events`
- Interactivity request URL -> `<BASE_URL>/api/slack/events`
- Slash command URLs -> `<BASE_URL>/api/slack/events`
- OAuth redirect URL -> `<BASE_URL>/api/slack/oauth/callback`
- Required bot events and command aliases

The Modal production deploy script runs this automatically unless you pass `-SkipSlackManifestSync`.

## 4) Token scope and multi-user behavior

- `SLACK_APP_CONFIG_TOKEN` is not channel-specific. It manages app configuration for your Slack app.
- With `ENABLE_SLACK_OAUTH=true`, each workspace gets its own bot token after install.
- In the same workspace, anyone can use PRFactory in any channel where the bot is invited.
- Cross-workspace self-serve installs use your OAuth install URL: `<BASE_URL>/api/slack/install`.

## 5) Validate

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1
```

Try:

```text
/prfactory Add a button to export invoices
```

When the bot is invited to a channel, it posts a short onboarding message and DMs the inviter with GitHub setup guidance.
When the app home is opened after install, PRFactory also sends a one-time setup DM reminder (including GitHub connect link).
The `/prfactory-github` command remains available but is no longer required for discoverability.
In non-mock production mode, the intake flow requires a target `org/repo` before build starts.
Default intake flow is minimal: prompt + repo, then auto-build.

## 6) Per-user GitHub sign-in (shared channels)

Set:
- `ENABLE_GITHUB_USER_OAUTH=true`
- `GITHUB_OAUTH_CLIENT_ID=...`
- `GITHUB_OAUTH_CLIENT_SECRET=...`
- `GITHUB_USER_OAUTH_REQUIRED=true`

Result:
- Each Slack user connects their own GitHub account.
- Builds run using the requester's connected GitHub identity.
- Users in the same channel/workspace do not share one GitHub account.
## 7) Enable multi-workspace installs

1. In Slack app settings, go to **Manage Distribution**.
2. Complete checklist items (including HTTPS request/redirect URLs).
3. Click **Activate Public Distribution**.
4. Use either:
- Slack-provided shareable URL from Manage Distribution, or
- your own install URL: `<BASE_URL>/api/slack/install`
