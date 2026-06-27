FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir imapclient requests pyyaml

COPY watcher.py /app/watcher.py

VOLUME ["/config"]

ENTRYPOINT ["python", "-u", "/app/watcher.py"]
