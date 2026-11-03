FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
RUN pip install --no-cache-dir playwright==1.64.1
# Install a patched Chrome for Testing build independently of Playwright's browser bundle.
ARG CHROME_VERSION=156.0.8078.80
RUN python3 -c 'import os, tempfile, urllib.request, zipfile; version = os.environ["CHROME_VERSION"]; url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chrome-headless-shell-linux64.zip"; urllib.request.urlretrieve(url, "/tmp/chrome.zip"); zipfile.ZipFile("/tmp/chrome.zip").extractall("/opt/job-desk-browser"); os.unlink("/tmp/chrome.zip")' \
    && chmod 755 /opt/job-desk-browser/chrome-headless-shell-linux64/chrome-headless-shell \
    && /opt/job-desk-browser/chrome-headless-shell-linux64/chrome-headless-shell --version
WORKDIR /app
COPY backend/*.py ./
USER pwuser
ENTRYPOINT ["python3", "-u", "/app/browser_service.py"]
