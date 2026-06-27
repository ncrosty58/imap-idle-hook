#!/usr/bin/env python3
"""
imap-idle-hook: fires a webhook when new mail arrives via IMAP IDLE.
Configure via /config/accounts.yaml or ACCOUNT_N_* environment variables.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from imapclient import IMAPClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(threadName)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger(__name__)

CONFIG_FILE     = os.environ.get('CONFIG_FILE', '/config/accounts.yaml')
IDLE_REFRESH    = 20 * 60   # restart IDLE every 20 min (RFC 2177 limit ~29 min)
RECONNECT_DELAY = 30        # seconds before reconnecting after error
IDLE_CHECK      = 30        # poll interval — keeps stop-event responsive


def load_config() -> dict:
    path = Path(CONFIG_FILE)
    if path.exists():
        log.info('loading config from %s', path)
        with path.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def load_accounts() -> list[dict]:
    cfg = load_config()

    if 'accounts' in cfg:
        return cfg['accounts']

    # Fall back to ACCOUNT_N_* env vars
    accounts = []
    i = 1
    while True:
        prefix = f'ACCOUNT_{i}_'
        email = os.environ.get(f'{prefix}EMAIL')
        if not email:
            break
        accounts.append({
            'email':    email,
            'password': os.environ.get(f'{prefix}PASSWORD', ''),
            'host':     os.environ.get(f'{prefix}HOST', ''),
            'port':     int(os.environ.get(f'{prefix}PORT', 993)),
            'security': os.environ.get(f'{prefix}SECURITY', 'SSL').upper(),
        })
        i += 1

    return accounts


def load_webhook() -> dict:
    cfg = load_config()
    webhook = cfg.get('webhook', {})

    # env vars override config file
    url = os.environ.get('WEBHOOK_URL') or webhook.get('url', '')
    method = os.environ.get('WEBHOOK_METHOD') or webhook.get('method', 'POST')

    headers = webhook.get('headers', {})
    if env_headers := os.environ.get('WEBHOOK_HEADERS'):
        try:
            headers.update(json.loads(env_headers))
        except json.JSONDecodeError:
            log.warning('WEBHOOK_HEADERS is not valid JSON, ignoring')

    return {'url': url, 'method': method.upper(), 'headers': headers}


def fire_webhook(webhook: dict, email: str, folder: str) -> None:
    url = webhook['url']
    if not url:
        log.warning('no WEBHOOK_URL configured, skipping')
        return

    payload = {
        'event':     'new_mail',
        'email':     email,
        'folder':    folder,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.request(
            method=webhook['method'],
            url=url,
            json=payload,
            headers=webhook['headers'],
            timeout=15,
        )
        resp.raise_for_status()
        log.info('%s: webhook fired → %s %s', email, resp.status_code, url)
    except requests.RequestException as e:
        log.error('%s: webhook failed: %s', email, e)


def watch(account: dict, webhook: dict, stop: threading.Event) -> None:
    email    = account['email']
    password = account['password']
    host     = account['host']
    port     = account['port']
    security = account.get('security', 'SSL').upper()
    use_ssl  = security == 'SSL'
    starttls = security == 'TLS'

    while not stop.is_set():
        try:
            log.info('%s: connecting', email)
            with IMAPClient(host, port=port, ssl=use_ssl) as client:
                if starttls:
                    client.starttls()
                client.login(email, password)
                client.select_folder('INBOX')
                client.idle()
                log.info('%s: IDLE active', email)
                idle_started = time.monotonic()
                while not stop.is_set():
                    responses = client.idle_check(timeout=IDLE_CHECK)
                    if stop.is_set():
                        client.idle_done()
                        return
                    if any(len(r) >= 2 and r[1] == b'EXISTS' for r in responses):
                        log.info('%s: new mail in INBOX', email)
                        threading.Thread(
                            target=fire_webhook,
                            args=(webhook, email, 'INBOX'),
                            daemon=True,
                        ).start()
                    if time.monotonic() - idle_started >= IDLE_REFRESH:
                        client.idle_done()
                        client.idle()
                        idle_started = time.monotonic()
        except Exception as e:
            if stop.is_set():
                return
            log.error('%s: %s — reconnecting in %ds', email, e, RECONNECT_DELAY)
            stop.wait(timeout=RECONNECT_DELAY)


def main() -> None:
    accounts = load_accounts()
    if not accounts:
        log.error('no accounts configured — set ACCOUNT_1_EMAIL/PASSWORD/HOST or mount /config/accounts.yaml')
        raise SystemExit(1)

    webhook = load_webhook()
    if not webhook['url']:
        log.warning('no webhook URL configured — new mail will be logged but not forwarded')

    log.info('starting watchers for %d accounts', len(accounts))

    threads = []
    for account in accounts:
        stop = threading.Event()
        t = threading.Thread(
            target=watch,
            args=(account, webhook, stop),
            daemon=True,
            name=account['email'],
        )
        t.start()
        threads.append((t, stop))

    for t, _ in threads:
        t.join()


if __name__ == '__main__':
    main()
