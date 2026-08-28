# Xray 镜像消费契约

本仓库**只消费**镜像，不构建、不发布。构建、发布流水线、tag 契约与 Docker Hub
Overview 全部归独立项目
[`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker)。

| 事项 | 归属 |
|---|---|
| Dockerfile、entrypoint、构建与审计脚本、发布流水线、tag 契约 | `xray-docker` |
| `xray_image` 变量、拉取与更新策略、容器运行参数、配置模板、订阅与监控 | 本仓库 |
| tag 语义或镜像行为变更的告知 | `xray-docker` 发布，本仓库消费 |

## `xray_image` 变量

定义在 `group_vars/all/main.yml:3`，当前值 `taoziyoyo2566/xray-docker:latest`。

消费点：

- `roles/reality_single/tasks/main.yml:116`（存在性检查）、`:124`（条件拉取）
- `roles/reality_multi/tasks/main.yml:101`、`:109`、`:116`
- `roles/reality_multi/templates/docker-compose.yml.j2:8`

## tag 契约

| 引用形式 | 语义 |
|---|---|
| `latest` | **唯一会移动的 tag**，指向最新 stable |
| `vX.Y.Z` | 对应上游 stable 版本。**不保证内容永久不变** —— 镜像定义变更时会重建并改指 |
| `vX.Y.Z-beta` | 对应上游 prerelease |
| `@sha256:...` | 唯一能保证内容绝对不变的引用方式 |

需要「内容绝对不变」时必须 pin digest，不能依赖版本 tag。

## 节点不会自动升级

这是设计行为，不是缺陷，但容易误判：

- 默认只在**本地无该镜像时**才拉取（`reality_single/tasks/main.yml:126`、
  `reality_multi/tasks/main.yml:111` 的 `when: images | length == 0`）。
- multi 模式的 compose 使用 `pull: never`（`reality_multi/tasks/main.yml:257`）。
- 无条件拉取的任务挂在 `update_image` tag 下，默认不执行
  （`reality_single/tasks/main.yml:133`、`reality_multi/tasks/main.yml:118`）。

结果：节点会长期停在首次拉取的那个 `latest`。`[实测]` 已观察到节点运行 8 个月前的
镜像（Xray `25.12.8`，构建于 2025-12-24），而 Docker Hub 的 `latest` 早已前移。

**因此：改了 `xray_image` 不等于节点会升级。** 必须显式刷新。

## 升级流程

1. 确认目标 tag 已在上游发布 —— 看
   [`xray-docker` 的 Actions](https://github.com/taoziyoyo2566/xray-docker/actions)
   或 [Docker Hub tag 列表](https://hub.docker.com/r/taoziyoyo2566/xray-docker/tags)。
2. 需要固定版本或 digest 时，改 `group_vars/all/main.yml` 的 `xray_image`。
3. 强制拉取：`./ansible-playbook deploy <host> --tags update_image`。
4. 完整部署以重建容器。
5. 按下节验收。

## 首次切到 `xray-docker` 必须盯的行为差异

以下差异据 `xray-docker` 侧记录，**本仓库尚未在任何节点上实测**，切换当天需现场确认：

| 项 | 变化 |
|---|---|
| 坏配置行为 | 新 entrypoint 在启动前执行 `xray run -test`。坏配置从「静默重启循环」变为「容器直接退出并打印解析错误」；`restart: always` 下表现为**容器停住**。这是最需要盯的一条 |
| geodata | 由 `/usr/bin/` 改为 `/usr/local/share/xray` 并设置 `XRAY_LOCATION_ASSET`，可在 `--read-only --cap-drop ALL` 下正常解析 |
| 镜像体积 | 约 154MB 降至约 104MB（amd64，未压缩） |
| 容器 UID | 仍为 `10000:10000`，与现有部署一致，无需改动 |
| HEALTHCHECK | 未设置 `XRAY_HEALTH_PORT` 时不做探测 |

## 回退

把 `xray_image` 改回旧引用（建议直接 pin digest），再用 `--tags update_image` 重拉。
旧仓库 `taoziyoyo2566/xray_docker` 已冻结不再更新，但既有 tag 与镜像仍然保留，
其 `latest` 停在 2026-08-26。

## 相关

- 计划与缺陷台账：[`docs/reviews/roadmap-unified-2026-08-27.md`](../reviews/roadmap-unified-2026-08-27.md)
- 剥离前的发布侧历史证据：[`phase1-image-release-2026-08-26.md`](../reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md)
