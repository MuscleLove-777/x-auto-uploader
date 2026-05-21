# X Auto-Post Recovery

Current target:

- One post per day at 12:00 JST.
- Fail the GitHub Actions run when X media auth is stale.
- Analyze recent account posts before each run when timeline access is available.
- Use `x_account_insights.json` to tune templates, CTA lines, and hashtags.

Required GitHub Actions secrets:

- `X_CONSUMER_KEY`
- `X_CONSUMER_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`
- `GDRIVE_FOLDER_ID_DEFAULT`
- Optional: `GDRIVE_FOLDER_ID_FRIDAY`
- Optional: `LINE_CHANNEL_TOKEN`
- Optional: `LINE_USER_ID`

To refresh X credentials:

1. Open X Developer Portal.
2. Set the app permission to `Read and Write`.
3. Regenerate Access Token and Secret after changing permission.
4. Update GitHub Actions secrets with the regenerated values.
5. Run `X Auto Upload` manually with `dry_run=false`.

Local checks:

```powershell
python -m py_compile upload.py trending.py tweet_and_pin.py x_account_insights.py
$env:X_DRY_RUN="true"
python upload.py
```

Notes:

- `x_account_insights.py` falls back gracefully if the current X API plan cannot read the timeline.
- The uploader still posts safely with default templates when timeline analysis is unavailable.
- `uploaded.json`, `x_account_insights.json`, and `failure_log.jsonl` are runtime state files and are ignored locally.
