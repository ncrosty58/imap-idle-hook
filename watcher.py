#!/usr/bin/env python3
"""
imap-idle-hook: fires an action when new mail arrives via IMAP IDLE.

Account sources (in priority order):
  1. ACCOUNTS_CMD  — command whose stdout is a JSON array of accounts
  2. /config/accounts.yaml (or CONFIG_FILE)
  3. ACCOUNT_N_* environment variables

Actions on new mail (both can be set simultaneously):
  - WEBHOOK_URL / webhook.url  — HTTP POST with JSON payload
  - ON_NEW_MAIL_CMD            — shell command to run
"""
import json
import logging
import os
import shlex
import subprocess
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
ACCOUNTS_CMD    = os.environ.get('ACCOUNTS_CMD', '')
ON_NEW_MAIL_CMD = os.environ.get('ON_NEW_MAIL_CMD', '')
IDLE_REFRESH    = 20 * 60   # restart IDLE every 20 min (RFC 2177 limit ~29 min)
RECONNECT_DELAY = 30        # seconds before reconnecting after error
IDLE_CHECK      = 30        # poll interval — keeps stop-event responsive
RELOAD_INTERVAL = 10 * 60   # re-run ACCOUNTS_CMD to pick up new accounts


def load_config() -> dict:
    path = Path(CONFIG_FILE)
    if path.exists():
        log.info('loading config from %s', path)
        with path.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def load_accounts() -> list[dict]:
    if ACCOUNTS_CMD:
        try:
            result = subprocess.run(
                shlex.split(ACCOUNTS_CMD),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                log.error('ACCOUNTS_CMD failed: %s', result.stderr.decode(errors='replace').strip())
                return []
            accounts = json.loads(result.stdout)
            log.info('ACCOUNTS_CMD returned %d accounts: %s', len(accounts), [a['email'] for a in accounts])
            return accounts
        except Exception as e:
            log.error('ACCOUNTS_CMD exception: %s', e)
            return []

    cfg = load_config()
    if 'accounts' in cfg:
        return cfg['accounts']

    accounts = []
    i = 1
    while True:
        email = os.environ.get(f'ACCOUNT_{i}_EMAIL')
        if not email:
            break
        accounts.append({
            'email':    email,
            'password': os.environ.get(f'ACCOUNT_{i}_PASSWORD', ''),
            'host':     os.environ.get(f'ACCOUNT_{i}_HOST', ''),
            'port':     int(os.environ.get(f'ACCOUNT_{i}_PORT', 993)),
            'security': os.environ.get(f'ACCOUNT_{i}_SECURITY', 'SSL').upper(),
        })
        i += 1

    return accounts


def load_webhook() -> dict:
    cfg = load_config()
    webhook = cfg.get('webhook', {})
    url     = os.environ.get('WEBHOOK_URL') or webhook.get('url', '')
    method  = os.environ.get('WEBHOOK_METHOD') or webhook.get('method', 'POST')
    headers = webhook.get('headers', {})
    if env_headers := os.environ.get('WEBHOOK_HEADERS'):
        try:
            headers.update(json.loads(env_headers))
        except json.JSONDecodeError:
            log.warning('WEBHOOK_HEADERS is not valid JSON, ignoring')
    return {'url': url, 'method': method.upper(), 'headers': headers}


def on_new_mail(webhook: dict, email: str, folder: str) -> None:
    if webhook['url']:
        payload = {
            'event':     'new_mail',
            'email':     email,
            'folder':    folder,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        try:
            resp = requests.request(
                method=webhook['method'],
                url=webhook['url'],
                json=payload,
                headers=webhook['headers'],
                timeout=15,
            )
            resp.raise_for_status()
            log.info('%s: webhook → %s %s', email, resp.status_code, webhook['url'])
        except requests.RequestException as e:
            log.error('%s: webhook failed: %s', email, e)

    if ON_NEW_MAIL_CMD:
        try:
            result = subprocess.run(
                shlex.split(ON_NEW_MAIL_CMD),
                capture_output=True,
                timeout=60,
                env={**os.environ, 'IMAP_EMAIL': email, 'IMAP_FOLDER': folder},
            )
            if result.returncode != 0:
                log.warning('%s: ON_NEW_MAIL_CMD stderr: %s', email,
                            result.stderr.decode(errors='replace').strip())
            else:
                log.info('%s: ON_NEW_MAIL_CMD done', email)
        except Exception as e:
            log.error('%s: ON_NEW_MAIL_CMD failed: %s', email, e)


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
                            target=on_new_mail,
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
    webhook = load_webhook()
    if not webhook['url'] and not ON_NEW_MAIL_CMD:
        log.warning('no WEBHOOK_URL or ON_NEW_MAIL_CMD set — new mail will only be logged')

    active: dict[str, tuple[threading.Thread, threading.Event]] = {}

    while True:
        accounts     = load_accounts()
        current_keys = {a['email'] for a in accounts}

        if not active and not accounts:
            log.error('no accounts configured — set ACCOUNTS_CMD, ACCOUNT_1_EMAIL/PASSWORD/HOST, or mount /config/accounts.yaml')
            raise SystemExit(1)

        for email in list(active):
            if email not in current_keys:
                log.info('%s: no longer in account list, stopping', email)
                _, stop = active.pop(email)
                stop.set()

        for account in accounts:
            email = account['email']
            if email in active and active[email][0].is_alive():
                continue
            if email in active:
                log.info('%s: thread died, restarting', email)
                active.pop(email)[1].set()
            stop = threading.Event()
            t = threading.Thread(
                target=watch,
                args=(account, webhook, stop),
                daemon=True,
                name=email,
            )
            t.start()
            active[email] = (t, stop)

        # Only reload periodically if using a dynamic account source
        if ACCOUNTS_CMD:
            time.sleep(RELOAD_INTERVAL)
        else:
            # Static config — just keep main thread alive
            threading.Event().wait()


if __name__ == '__main__':
    main()
