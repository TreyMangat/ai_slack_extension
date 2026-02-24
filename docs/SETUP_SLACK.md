# Slack Setup (Socket or HTTP Mode)

PRFactory uses Slack Bolt (Python) with:
- `SLACK_MODE=socket` for local/dev bot process
- `SLACK_MODE=http` for cloud/Modal (`/api/slack/events`, recommended)

Primary user command:
- `/prfactory <request>`

Compatibility/utility commands:
- `/feature <request>` (legacy alias)
- `/prfactory-github` (GitHub app install/login guidance)

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
- `SLACK_BOT_TOKEN=xoxb-...`
- `SLACK_SIGNING_SECRET=...`
- `SLACK_APP_ID=A...`
- `SLACK_APP_CONFIG_TOKEN=xoxe.xoxp-...` (App Configuration Token from `https://api.slack.com/apps`)

Optional:
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
- Required bot events and command aliases

The Modal production deploy script runs this automatically unless you pass `-SkipSlackManifestSync`.

## 4) Token scope and multi-user behavior

- `SLACK_APP_CONFIG_TOKEN` is not channel-specific. It manages app configuration for your Slack app.
- `SLACK_BOT_TOKEN` works for the whole workspace where the app is installed.
- In the same workspace, anyone can use PRFactory in any channel where the bot is invited.
- Different workspaces require separate app installation (and, for fully automatic multi-workspace support, a full Slack OAuth install flow).

## 5) Validate

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1
```

Try:

```text
/prfactory Add a button to export invoices
/prfactory-github
```

When the bot is invited to a channel, it posts a short onboarding message and DMs the inviter with GitHub setup guidance.
In non-mock production mode, the intake flow requires a target `org/repo` before build starts.
