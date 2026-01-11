FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Pre-install Playwright browsers at build time to avoid runtime downloads
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5).raise_for_status()" || exit 1

CMD ["python", "main.py"]
