FROM python:3.12-slim

# OPENBLAS/OMP em 1 thread: servidor de 0,5 CPU e o processo de correcao roda sob RLIMIT_AS
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     PIP_DISABLE_PIP_VERSION_CHECK=1     OPENBLAS_NUM_THREADS=1     OMP_NUM_THREADS=1     PORT=10000

WORKDIR /srv
COPY requirements.lock .
# Versoes exatas, inclusive as indiretas; --no-deps + pip check garantem que nada fora do lock entra.
# opencv-python-headless dispensa libGL.
RUN pip install --no-deps -r requirements.lock && pip check

COPY app ./app
RUN useradd --no-create-home --shell /usr/sbin/nologin --uid 10001 clavia
USER clavia

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s   CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"10000\")}/api/saude', timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-server-header --no-access-log --limit-concurrency 32 --timeout-keep-alive 5"]
