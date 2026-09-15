# xray_edge 工具镜像：节点应用器（一次性运行）与日志轮转循环。
# 合同：docs/reviews/data-plane-edge/plan-edge-compose-2026-09-15.md §3.3
# 构建（仓库根目录为上下文，只放行下方 COPY 的文件，见 Dockerfile.dockerignore）：
#   docker build -f docker/edge-tools/Dockerfile -t reality-edge-tools:local .
# python:3.13-alpine index digest，2026-09-15 于 Docker Hub 核对（基于 alpine 3.24）。
FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a

RUN apk add --no-cache logrotate docker-cli \
 && logrotate --version \
 && docker --version

COPY roles/xray_edge/files/xray_edge_apply.py /usr/local/bin/xray_edge_apply.py
COPY docker/edge-tools/logrotate-loop.sh /usr/local/bin/logrotate-loop
RUN chmod 0755 /usr/local/bin/xray_edge_apply.py /usr/local/bin/logrotate-loop

LABEL org.opencontainers.image.title="reality-edge-tools"
ENTRYPOINT []
CMD ["python3", "/usr/local/bin/xray_edge_apply.py", "--help"]
