FROM espocrm/espocrm:latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && pip3 install imapclient requests pyyaml --break-system-packages \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY watcher.py /opt/watcher.py
COPY espocrm/discover_accounts.php /opt/discover_accounts.php

# Discover accounts from EspoCRM's database and trigger cron.php on new mail.
# Both can be overridden at runtime if needed.
ENV ACCOUNTS_CMD="php /opt/discover_accounts.php"
ENV ON_NEW_MAIL_CMD="php /var/www/html/cron.php"

ENTRYPOINT ["python3", "-u", "/opt/watcher.py"]
