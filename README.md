# imap-idle-hook

A minimal Docker container that holds persistent IMAP IDLE connections and fires a webhook the moment new mail arrives. No polling — true push notification from your inbox.

## Why

IMAP IDLE (RFC 2177) lets the mail server push a notification to the client when new mail arrives, instead of the client polling on a timer. Most integrations still poll every minute or more. This container keeps a persistent IDLE connection open per mailbox and delivers sub-second webhook calls on arrival.

## Quickstart

### Environment variables

```yaml
services:
  imap-idle-hook:
    image: ghcr.io/ncrosty58/imap-idle-hook:latest
    restart: unless-stopped
    environment:
      WEBHOOK_URL: https://your-app.example.com/hooks/mail
      ACCOUNT_1_EMAIL: support@example.com
      ACCOUNT_1_PASSWORD: secret
      ACCOUNT_1_HOST: imap.example.com
```

### Config file

Mount a YAML file at `/config/accounts.yaml`:

```yaml
webhook:
  url: https://your-app.example.com/hooks/mail
  method: POST
  headers:
    Authorization: Bearer your-secret-token

accounts:
  - email: support@example.com
    password: secret
    host: imap.example.com
    port: 993
    security: SSL
```

```yaml
services:
  imap-idle-hook:
    image: ghcr.io/ncrosty58/imap-idle-hook:latest
    restart: unless-stopped
    volumes:
      - ./accounts.yaml:/config/accounts.yaml:ro
```

## Configuration reference

### Webhook

| Variable | Config key | Default | Description |
|---|---|---|---|
| `WEBHOOK_URL` | `webhook.url` | — | URL to POST to on new mail (required) |
| `WEBHOOK_METHOD` | `webhook.method` | `POST` | HTTP method |
| `WEBHOOK_HEADERS` | `webhook.headers` | — | JSON object of extra headers |

### Accounts

Env vars follow the pattern `ACCOUNT_N_*` where N starts at 1.

| Variable | Config key | Default | Description |
|---|---|---|---|
| `ACCOUNT_N_EMAIL` | `accounts[n].email` | — | IMAP login username (required) |
| `ACCOUNT_N_PASSWORD` | `accounts[n].password` | — | IMAP password (required) |
| `ACCOUNT_N_HOST` | `accounts[n].host` | — | IMAP server hostname (required) |
| `ACCOUNT_N_PORT` | `accounts[n].port` | `993` | IMAP port |
| `ACCOUNT_N_SECURITY` | `accounts[n].security` | `SSL` | `SSL`, `TLS` (STARTTLS), or `NONE` |

Environment variables override config file values for `webhook`. Accounts come from one source or the other, not both.

### CONFIG_FILE

Set `CONFIG_FILE` to override the default config path (`/config/accounts.yaml`).

## Webhook payload

```json
{
  "event": "new_mail",
  "email": "support@example.com",
  "folder": "INBOX",
  "timestamp": "2026-06-27T01:59:03.123456+00:00"
}
```

## How it works

One thread per mailbox. Each thread connects, selects INBOX, and enters IMAP IDLE. The server pushes an `EXISTS` response when mail arrives; the thread fires the webhook and re-enters IDLE. IDLE is restarted every 20 minutes to comply with RFC 2177's recommended limit. On connection failure the thread reconnects after 30 seconds.

## License

MIT
