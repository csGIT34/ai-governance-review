FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium for printing CSP reference doc pages to PDF artifacts.
# (playwright's --with-deps targets Ubuntu package names; install the
#  Debian bookworm equivalents explicitly.)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium \
    && apt-get update && apt-get install -y --no-install-recommends \
       libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
       libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libasound2 libpango-1.0-0 libcairo2 \
       fonts-liberation fonts-unifont \
    && rm -rf /var/lib/apt/lists/*

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000
# Migrations run on start. Fine for a single replica; with several replicas run
# `alembic upgrade head` as a separate job before rolling out instead.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
