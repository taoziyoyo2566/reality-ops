# 管理控制台镜像（web、report、status、bot 四个服务共用）。合同：docs/reviews/console/plan-console-phase1-2026-09-16.md §3.6、
# docs/reviews/console/plan-node-status-page-2026-09-18.md §3.3
# 构建（仓库根目录为上下文，只放行下方 COPY 的文件，见 Dockerfile.dockerignore）：
#   docker build -f docker/console/Dockerfile -t reality-console:local .
# status 服务的 Xray 客户端取自节点所用的同一固定 digest（group_vars/all/edge.yml edge_xray_image，静态链接）。
FROM taoziyoyo2566/xray-docker@sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7 AS xray

# 基础镜像与订阅服务相同（python:3.13-slim index digest，2026-09-15 于 Docker Hub 核对）。
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
COPY --from=xray /usr/bin/xray /usr/local/bin/xray

COPY docker/console/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# 复用订阅服务的 catalog 校验、链接规则与状态页渲染（不含其服务端与订阅内容渲染代码）。
COPY subs/__init__.py subs/catalog.py subs/links.py subs/statuspage.py /app/subs/
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
