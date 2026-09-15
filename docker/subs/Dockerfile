# 订阅服务镜像。合同：docs/reviews/subscription-service/plan-subscription-service-2026-09-15.md §3.7
# 构建（仓库根目录为上下文，只放行下方 COPY 的文件，见 Dockerfile.dockerignore）：
#   docker build -f docker/subs/Dockerfile -t reality-subs:local .
# python:3.13-slim index digest，2026-09-15 于 Docker Hub 核对。
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

COPY docker/subs/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY subs/ /app/subs/
RUN python -m compileall -q /app/subs

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    SUBS_DATA_DIR=/data SUBS_DB_DIR=/db SUBS_PORT=8100
USER 10001:10001
WORKDIR /app
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4).status == 200 else 1)"]
LABEL org.opencontainers.image.title="reality-subs"
CMD ["python", "-m", "subs.server"]
