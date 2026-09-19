# xray_edge 工具镜像：节点应用器（一次性运行）、日志轮转循环与节点 agent（上报与用户同步）。
# 合同：docs/reviews/data-plane-edge/plan-edge-compose-2026-09-15.md §3.3、docs/reviews/console/plan-console-phase1-2026-09-16.md §3.4
# 构建（仓库根目录为上下文，只放行下方 COPY 的文件，见 Dockerfile.dockerignore）：
#   docker build -f docker/edge-tools/Dockerfile -t reality-edge-tools:local .
# agent 经 Xray 命令行调用本节点 API 增删用户（plan-console-phase2 §3.3），取自节点所用的同一固定 digest（静态链接）。
FROM taoziyoyo2566/xray-docker@sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7 AS xray

# python:3.13-alpine index digest，2026-09-15 于 Docker Hub 核对（基于 alpine 3.24）。
FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a
COPY --from=xray /usr/bin/xray /usr/local/bin/xray

RUN apk add --no-cache logrotate docker-cli \
 && logrotate --version \
 && docker --version

COPY roles/xray_edge/files/xray_edge_apply.py /usr/local/bin/xray_edge_apply.py
COPY docker/edge-tools/logrotate-loop.sh /usr/local/bin/logrotate-loop
COPY docker/edge-tools/edge_reporter.py /usr/local/bin/edge-reporter
# 出口检测与切换（plan-egress-console）；检测代码与控制台共用同一个文件。
COPY docker/edge-tools/edge_egress.py /usr/local/bin/edge_egress.py
COPY console/egress_probe.py /usr/local/bin/egress_probe.py
RUN chmod 0755 /usr/local/bin/xray_edge_apply.py /usr/local/bin/logrotate-loop /usr/local/bin/edge-reporter

LABEL org.opencontainers.image.title="reality-edge-tools"
ENTRYPOINT []
CMD ["python3", "/usr/local/bin/xray_edge_apply.py", "--help"]
