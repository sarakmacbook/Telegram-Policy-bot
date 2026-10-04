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

## Connect a real Telegram bot

1. Create a bot with Telegram's **@BotFather**. Keep its token server-side.
2. Add the bot to the group(s) you are authorized to moderate. Grant the permissions you need, including **delete messages** if you want hide/restore actions. In BotFather, disable **Group Privacy** if the bot must receive regular group messages. Review Telegram's current rules and get notice/authorization from the group before monitoring.
3. Put these values in your process manager or secret store (not in the browser or Git):

   ```sh
   export APP_ENV=production
   export ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export BOT_TOKEN="<token from BotFather>"
   export WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export DATA_DIR="./data"
   export PORT=8000
   python app.py
   ```

   Serve this process behind HTTPS in production. If `ADMIN_TOKEN` is omitted, the app generates one at startup and prints it to the server log; set a stable secret explicitly instead. The web server is a small stdlib starter, not a hardened public-facing deployment stack—use a trusted reverse proxy, backups, rate limiting, and operational monitoring.

4. Register the publicly reachable HTTPS webhook URL. Use the same secret value as the server's `WEBHOOK_SECRET`:

   ```sh
   export PUBLIC_URL="https://your-domain.example"
   curl --fail-with-body -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
     -d "url=${PUBLIC_URL}/telegram/webhook" \
     -d "secret_token=${WEBHOOK_SECRET}" \
     -d 'allowed_updates=["message","edited_message","channel_post","edited_channel_post"]'
   ```

The console's **Telegram connection** section shows the setup checklist. “Bot configured” only means `BOT_TOKEN` is set; it does not verify the bot's permissions or webhook delivery.

## Review and hide behavior

- Unflagged text is checked in memory and discarded. Flagged text and associated review metadata are retained in SQLite (`data/moderation.sqlite3`). Flagged Telegram photos are downloaded to the private `data/media/` directory, subject to `MEDIA_MAX_BYTES`.
- The initial safe default is **review-first**: flagged messages are saved, but left in Telegram. Turn on **Auto-hide text-flagged messages** only after testing. `AUTO_HIDE=1` is a first-run default; dashboard settings are stored in the database and take precedence after the first run.
- **Queue all images for review** is an optional setting. It queues photos/images for people; it does not inspect image pixels. **Auto-hide visual-review media** removes every image queued by that setting, so it is a separate, off-by-default control.
- Admins can approve a still-visible message, dismiss a signal, hide a message with Telegram's `deleteMessage` API, or restore a hidden item. Telegram may reject deletion if bot permissions or message-age limits do not allow it. Telegram cannot undelete the original: **Restore** posts a new copy and cannot recreate the sender, timestamp, reply context, or original message ID. Restoration requires that the bot still has permission and the attachment was successfully archived.
- Saved image previews are served only through an authenticated API and are blurred in the web UI until a reviewer clicks **Click to reveal**. The app does **not** encrypt data at rest; use encrypted storage/backups and treat the server as sensitive because retained cases can contain harmful content.
- Private chats are not monitored by default. `MONITOR_PRIVATE_CHATS=1` opts them in, but a bot still only receives messages sent to it. The bot cannot see Telegram Secret Chats.

## Policy scope and legal caution

The default workspace scope is labeled **Cambodia**. This is context only—not an embedded Cambodian law database, legal interpretation, or compliance certification. Other jurisdictions can be listed in Settings, and an admin can add phrase checks for terms that need local review. Those checks are not a complete statement of law or Telegram policy. Laws, jurisdiction, context, and reporting obligations require qualified local counsel and human review.

The local phrase checks cover a small set of potential signals (threats, self-harm language, possible exploitation, targeted harassment, incitement, scams, and possible illicit trade). They are not an AI classifier, don't analyze an image's content, and are not exhaustive. Don't use a match alone to punish a user or make a legal report.

## Configuration

See [`.env.example`](.env.example) for optional settings. There are no third-party packages. The app creates `DATA_DIR` automatically; it is ignored by Git. Define a retention period for cases/media that meets your organization's policy and delete old records through your operational process.
