FROM python:3.12-slim-bookworm

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    DEBIAN_FRONTEND=noninteractive \
    CHROME_PATH=/usr/bin/chromium

WORKDIR /app

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://deb.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources; \
    else \
      sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://deb.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list; \
    fi \
 && printf 'Acquire::Retries "5";\nAcquire::https::Timeout "30";\nAcquire::http::Timeout "30";\n' >/etc/apt/apt.conf.d/99codex-speed \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY . /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5).raise_for_status()" || exit 1

CMD ["python", "main.py"]
