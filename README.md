# Telegram Policy Review Bot

A local-first Telegram moderation MVP with a private admin web console. The bot keeps a copy of messages that match configurable review signals, lets authorized moderators inspect the case, blurs saved images until explicitly revealed, and can hide or repost a reviewed copy to Telegram.

> **Important limits:** this cannot scan all of Telegram. A bot only receives updates Telegram makes available to it: typically group messages when it is an admin or BotFather privacy mode is disabled. Bots cannot access Secret Chats. It cannot make a reliable legal determination for Cambodia or any other country. Built-in checks are conservative phrase matches; they can miss violations and can flag harmless context. The image preview is a human-review feature, **not** automated image recognition.

## Run the local demo

Python 3.10+ is enough; the app uses only the standard library.

```bash
APP_ENV=demo ADMIN_TOKEN=demo-review-key PORT=8000 python app.py
```

Open `http://localhost:8000`. Demo mode opens the sample workspace automatically. Sample records are synthetic; actions only update those demo records and never contact Telegram. Demo mode returns its demo access key to the browser, so **never use `APP_ENV=demo` on a public deployment**.

Run the tests:

```bash
python -m unittest discover -s tests -v
```

## Deploy the console to Vercel

This repository includes a Vercel serverless entrypoint (`api/index.py`) and
`vercel.json`. The entrypoint reuses the same standard-library HTTP handler as
the local server, routes the dashboard and API through one function, and
bundles the `web/` assets automatically.

For a safe hosted preview, add a stable admin key and deploy demo mode:

```bash
npm install --global vercel
vercel login
vercel env add ADMIN_TOKEN preview
# Enter a long random value when prompted.
vercel env add APP_ENV preview       # enter: demo
vercel
```

Open the preview URL and sign in with the `ADMIN_TOKEN` value. To deploy to
production, add the same variables for the production environment and run
`vercel --prod`:

```bash
vercel env add ADMIN_TOKEN production
vercel env add APP_ENV production   # enter: production
vercel --prod
```

### Important Vercel storage limitation

Vercel Functions do not provide a durable local filesystem. The Vercel
adapter therefore defaults `DATA_DIR` to `/tmp/telegram-policy-bot`, which is
writable only for the lifetime of a warm function instance. SQLite cases,
archived media, saved settings, and UI-entered Telegram credentials can be
lost on a cold start and are not shared reliably between function instances.
Use the Vercel setup for a demo/preview unless you first replace the SQLite
and local-media storage with durable services. Do not put `DATA_DIR=./data`
in Vercel environment variables; the deployed bundle is read-only.

If you accept that limitation for a temporary test bot, configure all secrets
as Vercel environment variables rather than entering them in the dashboard:

- `APP_ENV=production`
- `ADMIN_TOKEN` — a long, stable admin key
- `BOT_TOKEN` — the token from @BotFather
- `WEBHOOK_SECRET` — a long, stable random secret

After the production deployment, register the webhook against the deployment
URL (Telegram requires HTTPS):

```bash
export BOT_TOKEN="<your bot token>"
export WEBHOOK_SECRET="<your webhook secret>"
export PUBLIC_URL="https://your-project.vercel.app"
curl --fail-with-body -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \\
  -d "url=${PUBLIC_URL}/telegram/webhook" \\
  -d "secret_token=${WEBHOOK_SECRET}" \\
  -d 'allowed_updates=["message","edited_message","channel_post","edited_channel_post"]'
```

The Vercel function must answer Telegram quickly; `vercel.json` sets a
60-second maximum duration. Vercel preview URLs can change, so use the stable
production domain for a webhook. See the real-bot section below for required
Telegram permissions and the moderation safety limits.

## Connect a real Telegram bot

1. Create a bot with Telegram's **@BotFather**. Keep its token private.
2. Add the bot to group(s) you are authorized to moderate. Grant permissions you need, including **delete messages** for hide actions. In BotFather, disable **Group Privacy** if the bot must receive regular group messages. Get appropriate notice/authorization from the group before monitoring.
3. Run the app behind HTTPS and set an admin key plus a persistent private data directory:

   ```sh
   export APP_ENV=production
   export ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export DATA_DIR="./data"
   export PORT=8000
   python app.py
   ```

   Then sign in, open **Settings → Telegram connection**, paste the bot token and the public HTTPS base URL for this server, and select **Verify bot & connect**. The app calls Telegram's `getMe` and `setWebhook` methods, creates a webhook secret if one is not already configured, and stores the credentials in `DATA_DIR/telegram_credentials.json` with file mode `0600`. Set `DATA_DIR` to private, persistent storage; the file is not encrypted at rest. The token is never returned by the API. If `BOT_TOKEN` is already supplied through the server environment, the UI can register its webhook without asking for the token, but that environment-managed token cannot be replaced in the UI.

   Alternatively, manage `BOT_TOKEN` and `WEBHOOK_SECRET` in your process manager or secret store and register the webhook manually:

   ```sh
   export BOT_TOKEN="<token from BotFather>"
   export WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export PUBLIC_URL="https://your-domain.example"
   curl --fail-with-body -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
     -d "url=${PUBLIC_URL}/telegram/webhook" \
     -d "secret_token=${WEBHOOK_SECRET}" \
     -d 'allowed_updates=["message","edited_message","channel_post","edited_channel_post"]'
   ```

   If `ADMIN_TOKEN` is omitted, the app generates one at startup and prints it to the server log; set a stable secret explicitly instead. The web server is a small stdlib starter, not a hardened public-facing deployment stack—use a trusted reverse proxy, backups, rate limiting, and operational monitoring.

The console's **Telegram connection** section shows bot-token and webhook-secret configuration status. Bot connectivity does not verify the bot's group permissions or guarantee Telegram can deliver every webhook update.

## Review and hide behavior

- Unflagged text is checked in memory and discarded. Flagged text and associated review metadata are retained in SQLite (`data/moderation.sqlite3`). Flagged Telegram photos are downloaded to the private `data/media/` directory, subject to `MEDIA_MAX_BYTES`.
- The initial safe default is **review-first**: flagged messages are saved, but left in Telegram. Turn on **Auto-hide text-flagged messages** only after testing. `AUTO_HIDE=1` is a first-run default; dashboard settings are stored in the database and take precedence after the first run.
- **DM the sender after a message is removed** is optional and off by default. When enabled, the bot attempts to send the rule category and reason after a manual or automatic removal. Telegram bots cannot initiate private chats: the sender must open the bot and press **Start** first. Telegram may still reject a DM (for example, if the user blocked the bot); that does not undo a successful removal. Anonymous admins and channel senders are not DM targets.
- **Queue all images for review** is an optional setting. It queues photos/images for people; it does not inspect image pixels. **Auto-hide visual-review media** removes every image queued by that setting, so it is a separate, off-by-default control.
- Admins can approve a still-visible message, dismiss a signal, hide a message with Telegram's `deleteMessage` API, or restore a hidden item. Telegram may reject deletion if bot permissions or message-age limits do not allow it. Telegram cannot undelete the original: **Restore** posts a new copy and cannot recreate the sender, timestamp, reply context, or original message ID. Restoration requires that the bot still has permission and the attachment was successfully archived.
- Saved image previews are served only through an authenticated API and are blurred in the web UI until a reviewer clicks **Click to reveal**. The app does **not** encrypt data at rest; use encrypted storage/backups and treat the server as sensitive because retained cases can contain harmful content.
- Private chats are not monitored by default. `MONITOR_PRIVATE_CHATS=1` opts them in, but a bot still only receives messages sent to it. A user can press **Start** in the bot chat to allow removal notices without enabling private-chat monitoring; those private messages are still discarded by default. The bot cannot see Telegram Secret Chats.

## Policy scope and legal caution

The default workspace scope is labeled **Cambodia**. This is context only—not an embedded Cambodian law database, legal interpretation, or compliance certification. Other jurisdictions can be listed in Settings, and an admin can add phrase checks for terms that need local review. Those checks are not a complete statement of law or Telegram policy. Laws, jurisdiction, context, and reporting obligations require qualified local counsel and human review.

The local phrase checks cover a small set of potential signals (threats, self-harm language, possible exploitation, targeted harassment, incitement, scams, and possible illicit trade). They are not an AI classifier, don't analyze an image's content, and are not exhaustive. Don't use a match alone to punish a user or make a legal report.

## Configuration

See [`.env.example`](.env.example) for optional settings. There are no third-party packages. The app creates `DATA_DIR` automatically; it is ignored by Git. Define a retention period for cases/media that meets your organization's policy and delete old records through your operational process.
