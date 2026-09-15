# 新数据面修订：xray_edge 整体纳入 Docker Compose

Status: **DRAFT — 待操作者评审。本文提议修改已批准的 S3 实施合同，批准前不得实施。**

Created: 2026-09-15 JST

被修订的合同：[`plan-single-instance-443-2026-09-15.md`](plan-single-instance-443-2026-09-15.md)（APPROVED，canary 验收完成）。
上位计划：[`roadmap-unified-2026-08-27.md`](../roadmap-unified-2026-08-27.md) §7 S3/S7、§7.1。批准后在被修订合同 §10 追加一条
已批准变更记录并链接本文；与路线图冲突时以路线图为准。

## 1. 目标

操作者要求（2026-09-15）：功能不要散布在宿主机各处，整体纳入 Docker 管理，便于维护与在其余节点快速部署。

使每个节点上的新数据面只由三样东西构成：**Docker（含 Compose 插件）+ `/opt/xray-edge` 一个目录 + 固定 digest 的镜像**。

1. `xray_edge` 容器改由 compose 项目管理，运行参数与现状逐项相同；
2. 日志轮转从宿主机 systemd timer + 宿主机 logrotate 改为 compose 内的轮转容器；
3. 应用器从宿主机 Python 脚本改为工具镜像内的一次性容器（D-E2）；
4. 删除节点上 `/opt/xray-edge` 之外的全部新数据面文件。

数据面行为（Xray 配置、用户、密钥、端口、DNS、订阅链接）不变。本修订应在 S7 向其余节点批量部署之前完成。

## 2. 批准时现状

- `[代码]` `/opt/xray-edge` 已包含 `bin/xray_edge_apply.py`、`state/desired.json`、`keys/`、`conf.d/`、`last-good/`、`logs/`。
- `[代码]` 在该目录之外的新数据面文件只有：`/etc/xray-edge/logrotate.conf`、`/etc/systemd/system/xray-edge-logrotate.{service,timer}`、
  `/var/lib/logrotate/xray-edge.status`；宿主机另需安装 `logrotate` 包（`roles/xray_edge/tasks/logrotate.yml`）。
- `[代码]` 容器由 `community.docker.docker_container` 创建（`roles/xray_edge/tasks/main.yml`），不带 compose 标签。
- `[代码]` 应用器通过 Docker CLI 执行 `docker run --rm`（生成密钥、`xray run -test` 候选目录，以 `-v <宿主机路径>` 挂载）、
  `docker exec`（API 增删用户）、`docker inspect`、`docker restart`。放入容器后，`-v` 的路径由宿主机 Docker 解析，
  因此容器内必须以**相同路径**挂载 `/opt/xray-edge`。
- `[代码]` 漂移检查在节点上用 `python3 -c` 解析旧实例用户（`main.yml`“读取旧实例的用户集合”）。
- `[上游]` Ansible 在被管主机上执行模块需要 Python；只要节点由 Ansible 管理，宿主机 Python 3 仍然存在。本修订去掉的是
  **新数据面自身**对宿主机 Python、logrotate 与 systemd 的依赖，不是 Ansible 的依赖。
- `[实测]` 2026-09-15：`dzire`、`usca`、`legend`、`spt` 均为 x86_64，Docker Compose 插件分别为 5.4.0、5.2.0、5.0.2、5.1.1；
  `community.docker` 5.1.0 含 `docker_compose_v2`。其余 10 台节点的架构与 Compose 插件未查。
- `[实测]` S3 剩余缺口（路线图 §7“S3 验收状态”）：last-good 自动恢复、API 改用户、SOCKS5 成员变更未在本地 e2e 或节点演练；
  首次实际轮转待观察。

## 3. 设计

### 3.1 目录

```text
/opt/xray-edge/
  compose.yaml        Ansible 渲染（0644 root）
  state/desired.json  期望状态（0600 root，不含私钥）
  keys/               节点 REALITY 密钥（0700 root）
  conf.d/             Xray 配置（0750 root:10000）
  last-good/          上一份可用配置（0700 root）
  logs/               Xray 访问与错误日志（0750 10000:10000）
  rotate/             logrotate.conf（0644 root）与状态文件目录（10000 可写）
```

`bin/` 删除（应用器进入镜像，D-E2 选 A 时）。

### 3.2 compose 项目 `xray-edge`

| 服务 | 镜像 | 要点 |
|---|---|---|
| `xray` | `edge_xray_image`（现有固定 digest） | `container_name: xray_edge`；端口、DNS、用户 `10000:10000`、`read_only`、`tmpfs`、`cap_drop ALL`、`no-new-privileges`、`pids_limit`、内存与 swap、`nofile`、`json-file` 日志上界全部沿用现值；挂载 `conf.d`（只读）与 `logs` |
| `logrotate` | 工具镜像 | 用户 `10000:10000`，`network_mode: none`，`read_only`，`cap_drop ALL`；循环执行 `logrotate -s /rotate/state/status /rotate/logrotate.conf` 后休眠 `edge_logrotate_interval`（默认 3600 秒）；挂载 `logs`（读写）与 `rotate`；内存上限 32MB；轮转规则与现有 `logrotate.conf` 相同（daily、maxsize、copytruncate、compress、rotate 14） |
| `applier` | 工具镜像 | `profiles: ["tools"]`，`docker compose up` 不启动；Ansible 以 `docker compose run --rm applier apply|verify` 调用。以 root 运行，挂载 `/var/run/docker.sock` 与 `/opt/xray-edge:/opt/xray-edge`（同路径），`network_mode: none` |

- 容器名保持 `xray_edge`：应用器、`edge-remove.yml` 与 `docs/project-memory.md` 中已记录的核对命令不变。
- `docker compose up -d` 只在 compose 定义变化时重建 `xray`；用户变化仍由应用器经 API 完成，不重建容器。

### 3.3 工具镜像 `reality-edge-tools`

- 仓库 `docker/edge-tools/Dockerfile`：基础镜像 `python:3.13-alpine` 以 digest 固定，安装 `logrotate` 与 `docker-cli`，
  复制 `xray_edge_apply.py`。应用器版本随镜像 digest 固定，不再单独下发脚本。
- 构建在控制端进行。推送仓库时以 **digest** 引用（`edge_tools_image`），与 `edge_xray_image` 同样管理；以 `docker save`/`load`
  分发时镜像没有仓库 digest，改以**镜像 ID** 引用并在部署时核对。
- 分发方式见 D-E1。

### 3.4 Ansible 变化

- `roles/xray_edge/tasks/main.yml`：以 `docker_compose_v2` 替换 `docker_container`；应用器与 `verify` 改为 `docker compose run --rm applier`；
  漂移检查中的 `python3 -c` 改为在工具镜像中执行，或保留为 Ansible 侧解析（实现时选择输出更少的方式）。
- `roles/xray_edge/tasks/logrotate.yml`：只渲染 `/opt/xray-edge/rotate/logrotate.conf`（保留 `logrotate --debug` 校验，改在工具镜像内执行），
  删除宿主机包安装与 systemd 任务。
- **一次性清理任务**（从 S3 形态迁移）：停用并删除 `xray-edge-logrotate.timer/.service`、`/etc/xray-edge`、
  `/var/lib/logrotate/xray-edge.status`、`/opt/xray-edge/bin`。宿主机 `logrotate` 包不卸载（系统其他日志可能使用）。
- **容器接管**：现有 `xray_edge` 容器没有 compose 标签，`compose up` 会因容器名冲突失败。迁移时先删除该容器再 `compose up`，
  中断约数秒；`keys/` 与 `conf.d/` 保留，公钥与配置不变。canary 节点上只有 `test` 使用新实例，真实用户不受影响。
- `edge-remove.yml`：改为 `docker compose down` 后删除目录（默认保留 `keys/`），并包含上面的旧形态清理，便于任一形态都能回滚。

### 3.5 不变项

期望状态 schema、应用器逻辑（`validate`/`render`/`classify`/`user_diff`、原子替换、last-good）、`conf.d` 布局、节点密钥、
Xray 镜像 digest、端口与 IPv6 发布、容器 DNS 覆盖、出站竞速开关、测试链接与 Clash 片段、旧 single/multi 实例。

## 4. 范围与非目标

- 范围：`roles/xray_edge`、`edge.yml`、`edge-remove.yml`、`docker/edge-tools/`、`group_vars/all/edge.yml`、`tests/edge/`、
  被修订合同 §10 记录；`dzire`、`usca`、`legend` 的逐台迁移。
- 顺带关闭 S3 缺口：本地 e2e 增加 last-good 自动恢复、API 改用户（UUID 与 short_id 变更）与 SOCKS5 成员变更。
- 非目标：旧 single/multi；monitor（S6 容器化）；S4 订阅服务（见其独立计划）；Xray 配置或镜像变更；其余节点部署（S7）。

## 5. 前提、未知与关闭方式

1. **容器内 logrotate 以 UID 10000 运行**：`copytruncate`、`compress` 与状态文件写入是否可行，logrotate 对配置文件属主与权限的检查
   是否通过。关闭：本地 compose 测试，用小 `maxsize` 强制轮转并检查 `.gz` 与状态文件。
2. **同路径挂载下应用器的 `docker run -v`**：候选目录与密钥生成是否正常。关闭：本地 e2e 全部检查以 compose 形态重跑。
3. **Compose 插件版本差异**（5.0.2–5.4.0）：`profiles`、`run --rm` 行为。关闭：本地测试使用最低版本特性集；canary 三台覆盖三个版本。
4. **其余 10 台节点的架构与 Compose 插件**：关闭：S7 预检只读检查；缺少插件的节点记为 gap，安装插件属宿主机变更，另行授权。
   若出现 arm64 节点，工具镜像需多架构构建，回到 D-E1。
5. **小内存节点**（如 `jp05` 469MB）：轮转容器的常驻内存。关闭：本地测量；超过 20MB 时回到本文评估。

## 6. 验收标准

### 6.1 本地（控制端，无外部写入）

1. 工具镜像构建成功，基础镜像以 digest 固定；`docker compose config` 通过，渲染出的 `xray` 服务与现有 `docker_container` 参数逐项一致
   （对照表测试）。
2. `tests/edge/test_render.py` 全部通过（应用器逻辑不变）。
3. `tests/edge/e2e_local.py` 以 compose 形态运行，原有 21 项检查全部通过，并新增：last-good 自动恢复（重启后校验失败 → 恢复并重启 →
   旧用户仍可连接）、UUID 变更与 short_id 变更、SOCKS5 成员增减。
4. 轮转容器：强制轮转生成 `.gz`，`copytruncate` 后 Xray 继续写入同一文件，状态文件更新；常驻内存记录在案。
5. 迁移与移除：在本地模拟 S3 形态（非 compose 容器 + 宿主机式目录）后执行迁移任务，公钥与 `conf.d` 哈希不变；`edge-remove` 后除 `keys/`
   外无残留。
6. `edge.yml`、`edge-remove.yml` 语法检查通过；`--list-tasks` 不含旧角色任务。

### 6.2 canary 迁移（`dzire` → `usca` → `legend`，逐台另行授权）

1. 迁移前后旧实例指纹一致。
2. `xray_edge` 带 compose 标签运行，镜像 `fd502666`，重启 0；端口、DNS、内存与日志参数与迁移前 `docker inspect` 对比一致；
   公钥与 `conf.d` 哈希不变；用户数不变；`test` 链接无需重新导入即可连接。
3. `/etc/xray-edge`、两个 systemd 单元、`/var/lib/logrotate/xray-edge.status`、`/opt/xray-edge/bin` 不存在；`systemctl list-timers` 无 `xray-edge`。
4. `logrotate` 容器运行；手动触发一次成功；次日检查按天轮转并压缩。
5. 演练：应用器容器增删用户经 API 不重启；容器重启后用户一致；`edge-remove.yml`（保留密钥）后重新部署，公钥与 `conf.d` 相同。

## 7. 风险

- 应用器容器挂载 Docker socket，等同宿主机 root；与现状（宿主机 root 运行脚本）权限相同，只在部署时一次性运行、不常驻。
- 工具镜像成为新的供应链输入：以 digest 固定并记录构建来源；构建输入变化需要重新验收 §6.1。
- 迁移时 `xray_edge` 重建造成数秒中断：canary 阶段只影响 `test`；S7 中新节点首次部署即为 compose 形态，无此步骤。
- 一个节点同时存在两种形态会增加排查成本：三台 canary 迁移完成前不向其他节点部署。

## 8. 待操作者确认

1. **D-E1 工具镜像分发**：
   - **A. 推送到 Docker Hub（与现有 Xray 镜像同一账号）并以 digest 拉取**（建议用于 S7）：节点只需拉取，部署最快，与 `edge_xray_image`
     管理方式一致。推送属于外部发布，需要单独授权；镜像只含开源工具与应用器代码，不含秘密。
   - **B. 控制端构建后由 Ansible `docker save` 传到节点 `docker load`**：不经镜像仓库；每台节点多一次约数十 MB 的传输。
   建议：canary 用 B（不需外部发布即可开始），S7 批量部署前决定是否改为 A。
2. **D-E2 应用器是否进入镜像**：
   - **A. 进入工具镜像**（建议）：轮转已经需要工具镜像，应用器版本随镜像 digest 固定，节点目录中不再有可执行脚本。
   - **B. 保留为 `/opt/xray-edge/bin` 下的脚本**，由宿主机 Python（Ansible 本来就需要）执行：不需要挂载 Docker socket，但版本随文件下发。
3. **D-E3 执行时机**：建议在 S4 本地实现期间并行完成本修订的 §6.1，S4 上线 `spt` 之前完成三台 canary 迁移，以便 S4 验收时节点已是最终形态。

## 9. 授权边界

本文为 DRAFT。批准后仅授权工作树内实现与 §6.1 本地验证（含控制端构建镜像与本地 compose 运行），并在被修订合同 §10 追加变更记录。
以下各自单独授权：Git 发布；每台节点的迁移、部署、移除或重启；向任何镜像仓库推送；在节点上安装或升级 Docker、Compose 插件或其他软件包。
