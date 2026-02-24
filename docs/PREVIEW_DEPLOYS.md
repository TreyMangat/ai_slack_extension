# Preview Deploys (Cloudflare Pages Recommended)

## Goal
For UI requests, reviewers should click a link from the PR and see the feature without local setup.

## Recommended default: Cloudflare Pages

### One-time setup (human)
1. In Cloudflare Pages, create a project connected to your active target GitHub repo.
2. Set production branch (usually `main`).
3. Confirm preview deployments are enabled for pull requests (default for GitHub-integrated Pages projects).

### Orchestrator config
Set in `.env`:

```env
PREVIEW_PROVIDER=cloudflare_pages
CLOUDFLARE_PAGES_PROJECT_NAME=<your-pages-project-name>
CLOUDFLARE_PAGES_PRODUCTION_BRANCH=main
```

### Callback wiring for status automation
Use repo secrets so GitHub workflows can report PR/preview status back:

- `FEATURE_FACTORY_CALLBACK_URL`
- `FEATURE_FACTORY_WEBHOOK_SECRET` (must match orchestrator `INTEGRATION_WEBHOOK_SECRET`)

Use your CI/deploy workflow to send:
- `pr_opened` / `build_failed` callbacks when code runner status changes
- `preview_ready` callback when preview deployment is ready

## How reviewers find the preview
- Open PR -> Checks tab
- Open Cloudflare Pages check/deployment
- Click View deployment

When callback succeeds, Feature Factory updates status to `PREVIEW_READY` and posts preview URL to Slack/thread.

## Fallback: GitHub Pages (less ideal)
Use only if Cloudflare Pages integration is not available.
GitHub Pages is usually branch/environment oriented, not per-PR by default, so reviewer UX is weaker.

## Notes for backend-dependent UI
For local POC, default is static frontend preview.
If UI needs live backend behavior, add a dedicated preview backend later.
