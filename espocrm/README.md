# imap-idle-hook: EspoCRM edition

A drop-in sidecar for EspoCRM that replaces cron-based email polling with true IMAP IDLE push notification. Email accounts are discovered automatically from EspoCRM's own database — no separate configuration required.

## How it works

The container shares EspoCRM's data volume. On startup (and every 10 minutes), it reads EspoCRM's `config-internal.php` to get database credentials, queries the `inbound_email` and `email_account` tables, decrypts IMAP passwords using EspoCRM's own AES key, and opens a persistent IMAP IDLE connection to each active account. When new mail arrives, it immediately runs `php cron.php` — the same script EspoCRM's cron job calls — so email is processed in seconds rather than up to a minute later.

Adding or removing an email account in EspoCRM's admin UI is automatically reflected within 10 minutes. No config changes, no restarts.

## Setup

Add the sidecar to your existing EspoCRM stack:

```yaml
services:
  espocrm:
    image: espocrm/espocrm:latest
    # ... your existing config ...
    volumes:
      - espocrm_html:/var/www/html

  espocrm-imap-watcher:
    image: ghcr.io/ncrosty58/imap-idle-hook:espocrm
    restart: unless-stopped
    depends_on:
      - espocrm
    volumes:
      - espocrm_html:/var/www/html   # required: shares EspoCRM's files and config
    networks:
      - your_db_network              # required: must reach EspoCRM's database

volumes:
  espocrm_html:
```

No environment variables needed. The container reads everything it needs from EspoCRM's own config.

## Requirements

- EspoCRM 8.x or later
- The sidecar must be on the same Docker network as the database EspoCRM uses
- The `espocrm_html` volume must be shared between EspoCRM and this container

## What triggers cron.php

EspoCRM's `cron.php` handles all background tasks: email import, workflow actions, notifications, mass mail, cleanup. This container fires it immediately on new mail arrival (instead of waiting for the next scheduled cron run), and also runs it every 2 minutes as a fallback for non-email tasks.

## Overriding defaults

The two core env vars are set in the image but can be overridden:

| Variable | Default | Description |
|---|---|---|
| `ACCOUNTS_CMD` | `php /opt/discover_accounts.php` | Command that returns a JSON array of IMAP accounts |
| `ON_NEW_MAIL_CMD` | `php /var/www/html/cron.php` | Command to run when new mail arrives |

To add a webhook call in addition to cron.php, set `WEBHOOK_URL`.
