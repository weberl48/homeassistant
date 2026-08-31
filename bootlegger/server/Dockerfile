FROM python:3.12-slim

WORKDIR /srv/bootlegger
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The hands need a browser. Sleeper's lineup editor is a React app with no
# public write API for a swap — the change is an `update_matchup_leg` GraphQL
# mutation the page fires for you — so actuation means driving the real UI.
#
# This lands in the shared image rather than a separate one because deploy.sh
# builds once and runs four containers from it; only bootlegger-hands ever
# launches the browser, and the cost to the other three is disk, not memory.
# Playwright publishes a linux-arm64 Chromium, which is what the Pi needs.
#
# The path is set BEFORE the install on purpose: exported afterwards it would
# point the runtime at an empty directory while the browser sat in the build
# user's cache, which fails only when a swap is finally attempted.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright==1.55.0 \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY hands ./hands
COPY pyproject.toml .

ENV BOOTLEGGER_DB=/data/bootlegger.db \
    BOOTLEGGER_AUDIT_DIR=/data/audit

EXPOSE 8484
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8484"]
