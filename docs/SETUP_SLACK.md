# Slack setup (Socket Mode)

This scaffold uses **Slack Bolt (Python)** with **Socket Mode**.
The `/feature` flow is designed for non-technical users:
- guided thread-based chat intake (no popup modal)
- automatic clarification prompts if required fields are missing
- **Add details in chat** action to update and revalidate the same request

## Steps

0. (Optional) Import the manifest template in `docs/slack_app_manifest.yaml`
1. Create a Slack app
2. Enable Socket Mode
3. Create an App Token:
   - Starts with `xapp-...`
   - Add scope: `connections:write`
4. Add a Bot user and install the app into your workspace
5. OAuth scopes (minimum viable):
   - `chat:write`
   - `commands`
   - `channels:read`
   - `channels:history`
   - `channels:join` (recommended)
   - `groups:read`
   - `groups:history`
   - `im:read`
   - `im:history`
   - `mpim:read`
   - `mpim:history`

7. Event subscriptions (bot events):
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `message.mpim`

6. Create a Slash Command:
   - `/feature`

## Configure `.env`

Set:
- `ENABLE_SLACK_BOT=true`
- `SLACK_BOT_TOKEN=xoxb-...`
- `SLACK_APP_TOKEN=xapp-...`
- `REVIEWER_ALLOWED_USERS=U0123ABC,U0456DEF` (recommended)

Optional restrictions:
- `SLACK_ALLOWED_CHANNELS=C0123ABC,C0456DEF`
- `SLACK_ALLOWED_USERS=U0123ABC,U0456DEF`
- `SLACK_REQUIRE_MENTION=true`
- `REVIEWER_CHANNEL_ID=C09REVIEW`

Restart docker compose with the Slack profile:

```powershell
docker compose --profile slack up --build
```

After changing scopes/event subscriptions, reinstall or re-authorize the app in your workspace.

Validation helper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1
```

Try it:

```text
/feature Add a button to export invoices
```

The bot will ask one question at a time in the thread, for example:
- What do you want to build?
- What problem are users facing?
- Why is this needed now?
- Attach request if applicable (links/files)
- Do you know what project/repo this belongs to?

If the request lands in `NEEDS_INFO`, the bot posts clarifying questions in thread.
Use **Add details in chat** on the request message to provide missing information.
