# 管理控制台镜像（web 与 report 两个服务共用）。合同：docs/reviews/console/plan-console-phase1-2026-09-16.md §3.6
# 构建（仓库根目录为上下文，只放行下方 COPY 的文件，见 Dockerfile.dockerignore）：
#   docker build -f docker/console/Dockerfile -t reality-console:local .
# 基础镜像与订阅服务相同（python:3.13-slim index digest，2026-09-15 于 Docker Hub 核对）。
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

COPY docker/console/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# 复用订阅服务的 catalog 校验与链接规则（不含其服务端与渲染代码）。
COPY subs/__init__.py subs/catalog.py subs/links.py /app/subs/
COPY console/ /app/console/
RUN python -m compileall -q /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
USER 10002:10002
WORKDIR /app
EXPOSE 8200 8201
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('CONSOLE_PORT', '8200'), timeout=4).status == 200 else 1)"]
LABEL org.opencontainers.image.title="reality-console"
CMD ["python", "-m", "console.web_app"]
