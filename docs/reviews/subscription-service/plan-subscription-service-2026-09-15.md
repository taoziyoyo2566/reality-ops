# S4 订阅服务：每用户凭据 · 直接返回正文 · 防泄漏客户端配置

Status: **APPROVED — 2026-09-15 操作者按 §8 建议决定 D1–D5（D1 选 B）；仅授权工作树内实现与 §6.1 本地验证（§9）。**

Created: 2026-09-15 JST

上位计划：[`roadmap-unified-2026-08-27.md`](../roadmap-unified-2026-08-27.md) §4 C03/C04/C13/C17、§7 S4、§7.1（U3、U5、
migrate 步骤）、§7.2、§9 第 8、10 项。数据面合同：[`plan-single-instance-443`](../data-plane-edge/plan-single-instance-443-2026-09-15.md)。
本文只细化 S4 的实施合同；与路线图冲突时以路线图为准并先修订路线图。

## 1. 目标

新增一个独立的订阅服务，替代“共享 token + 可枚举用户名 + 公开 Gist”的现有链路，并成为 S7 迁移的开关：

1. **每用户独立凭据（U3，C03）**：不可枚举、可单独轮换与吊销；A 的凭据取不到 B 的内容；缺少凭据数据时拒绝服务。
2. **直接返回正文（C04）**：服务本身渲染订阅内容，不经过 Gist 或任何公开落点。
3. **按节点迁移状态输出链接（§7.1）**：未迁移节点输出旧链接，已迁移节点只输出 443 链接；节点迁移随客户端刷新生效，不需要通知用户。
4. **按客户端类型下发配置（U5，§7.2）**：v2rayN、Shadowrocket 取 `vless://` 订阅；Clash（Mihomo）取完整 profile，
   分“隐私”“分流”两种模式，各自独立地址。
5. **二维码与深链接导入（§7.2 第 6 项）**：每用户页面展示订阅地址的二维码与 `clash://install-config` 深链接。

完成后，用户只需手动更换一次订阅地址（§9 第 8 项）；此后节点迁移、换钥、换端口都随订阅刷新生效。

## 2. 批准时现状

- `[代码]` 现有链路：`deploy.yml` 在控制端写 `/opt/reality/users/<用户>_<节点>.json`；`generate_subs_gist.py` 读取后按用户聚合、
  base64 编码，PATCH 到一个 Gist（每用户 `<用户>` 仅 IPv4、`<用户>-full` 含 IPv6 字面量两份）；monitor 服务
  （`roles/monitor/templates/server.py.j2`）的 `GET /{sub_id}?token=<共享 token>` 记录访问后 **307 重定向**到
  `gist.githubusercontent.com/<用户>/<Gist ID>/raw/<用户名>`。
- `[代码]` 由此可见：`sub_id` 就是用户名（可枚举）；token 全体共用；Gist 原始地址不经 token 即可读取（C03、C04）；
  `SUBS_REQUIRE_TOKEN` 为真但 `SUBS_TOKEN` 为空时不校验（fail-open）。
- `[实测]` 2026-09-15 `spt`（控制端即 `mail.taoziyoyo.com`）：`cloudflared` 以 `tunnel run --token` 运行，即隧道路由由
  Cloudflare 控制台远程管理；`reality-monitor.service` 运行中；`/opt/reality/users` 为 `0755`，332 个文件均为 `0644`，
  字段为 `id`、`subscription`、`user`（含 UUID 与公钥，不含私钥）。按决定 9 旧系统不改，此处只记录。
- `[推论]` `subs.taoziyoyo.com` 经同一隧道指向 monitor（`127.0.0.1:8000`）；控制台路由未查看。
- `[代码]` 新数据面测试链接由 `roles/xray_edge/tasks/subs.yml` 在控制端生成，仅测试用户：域名地址、Vision 与可选 XHTTP、
  Mihomo 片段（`stream-one`）。节点公钥来自节点上应用器 `verify` 的输出（`edge_public_key`）。
- `[代码]` 用户可访问的节点由 `roles/xray_edge/tasks/acl.yml`（`deploy.yml` pre_tasks 的逐字复制）在每个节点的
  Ansible 上下文中计算；`node_endpoint` 与 IPv6 判断依赖该节点的 facts。
- `[实测]` U5 可行性（路线图 §7.2）：STUN 经节点返回节点地址；隐私模式 Mihomo profile `mihomo -t` 通过；
  设备端 ipleak 检测与二维码导入按操作者决定推迟到本阶段。
- `[代码]` 33 份用户档案仍含 `private_key`（C02）；新服务的任何数据文件不得包含它。
- `[实测]` 2026-09-15 `spt`：x86_64，Docker Compose 插件 5.1.1，可用内存约 1.1GB；`community.docker` 5.1.0（含 `docker_compose_v2`
  与 `docker_image_build`）。
- 操作者要求（2026-09-15）：功能不要散布在宿主机各处，整体纳入 Docker 管理，便于维护与快速部署。

## 3. 设计

### 3.1 组成与隔离

```text
控制端 Ansible（subs.yml，只读访问节点）
  ├─ 每节点：ACL 结果、endpoint、IPv6 能力、迁移状态、443 公钥 / XHTTP path
  ├─ 旧链接：读取 /opt/reality/users/<用户>_<节点>.json（只读）
  └─ 生成 catalog.json + token 哈希表 ──原子替换──> spt:/opt/reality-subs/data/

spt:/opt/reality-subs/            ← 整个功能只占这一个目录，宿主机只需要 Docker 与 Compose 插件
  ├─ compose.yaml                 ← Ansible 渲染
  ├─ data/                        ← catalog.json、tokens.json（只读挂载）
  ├─ db/                          ← 访问日志 SQLite（唯一可写挂载）
  └─ secrets/                     ← 仅 D1 选 B 时存在：隧道 token（0600）

compose 项目 reality-subs
  ├─ subs         请求时：token 哈希 → 用户 → 从 catalog 渲染（纯函数）→ 返回正文
  └─ cloudflared  仅 D1 选 B：独立隧道，转发到 http://subs:8100，宿主机不开端口

用户 ──HTTPS──> Cloudflare ──隧道（D1）──> subs:8100
```

- 不创建宿主机用户、venv 或 systemd 单元，不改 monitor 代码与进程。monitor 持有管理/统计 token，订阅服务持有全体用户的
  UUID，二者在不同容器与目录中，失陷面分开。
- 删除整个功能 = `docker compose down` + 删除 `/opt/reality-subs` + 删除镜像；迁到另一台装有 Docker 的主机 = 复制 compose、数据与
  （选 B 时）隧道 token 后 `docker compose up -d`。
- 服务只读数据目录，不写配置、不调用 shell、不访问节点、不挂载 Docker socket。catalog 按 mtime 重新加载，发布新 catalog 不需要重启。
- 渲染器是纯函数（catalog + 用户 + 格式 + 模式 → 正文），与服务框架分开，便于 golden 测试；链接拼接统一走标准 URL 编码，
  取代多处手工拼接（C13）。

### 3.2 catalog（期望数据）

```json
{
  "schema": 1,
  "generated_at": "…",
  "nodes": {
    "usca": {"state": "migrated", "label": "usca",
             "edge": {"endpoint": "<域名>", "port": 443, "sni": "…", "public_key": "…",
                      "xhttp": {"enabled": true, "path": "…"}},
             "legacy": null}
  },
  "users": {
    "alice": {"nodes": {"usca": {"uuid": "…", "short_id": "…"},
                        "jp10": {"legacy_links": ["vless://…"]}}}
  }
}
```

- 节点 `state`：`legacy`（只输出旧链接）或 `migrated`（只输出 443 链接）。由新变量 `subs_node_states` 按节点显式设置，
  **默认 `legacy`**。并行期（expand 后、migrate 前）仍为 `legacy`，因为订阅切换就是 §7.1 的 migrate 动作本身。
- 用户可访问节点集合 = 与 `edge.yml` 相同的 ACL 结果；不在本阶段引入 grade/features（S1/S5）。
- `legacy_links` 原样取自旧系统输出，保持与现有 Gist 相同的内容，只做解析校验，不重写。
- `test` 只在新实例上存在（`edge_extra_users`），没有旧链接：某节点为 `legacy` 时 `test` 的订阅里不出现该节点，为 `migrated` 时出现。
  canary 期间用这一点在不影响真实用户的前提下验证切换，但切换同一节点对真实用户同样生效，因此 §6.3 只在尚无真实用户使用新地址时执行。
- catalog 不含：私钥、订阅 token 明文、用户档案其他字段。

### 3.3 凭据（U3）

- 每用户一个 token：32 字节随机数，URL-safe base64（43 字符）。明文保存在 Ansible vault（`vault_subs_tokens`，按用户名索引），
  服务端只保存 `SHA-256(token)` → 用户名的映射。查表使用哈希后的定长比较，服务端数据泄漏不直接暴露可用 token。
- 轮换：vault 中换值并重新发布，旧 token 立刻失效。吊销：从 vault 删除该用户，或从 catalog 移除该用户；两者任一缺失即 404。
- 发布前校验：每个 catalog 用户都有 token、token 无重复、长度与字符集合规；任一不满足则发布失败（fail closed）。
- 服务启动或重载时 token 表或 catalog 缺失、损坏、schema 不符：全部请求返回 503，不回退到旧数据以外的任何内容。
- `test` 用户使用同一机制，作为 canary 凭据。

### 3.4 接口

| 路径 | 内容 | 客户端 |
|---|---|---|
| `GET /s/<token>` | 用户页面：节点列表（名称）、各格式地址、二维码、Clash 深链接、隐私/分流说明与不可解决项（§7.2 第 4 项） | 浏览器 |
| `GET /s/<token>/v2ray` | base64 编码的 `vless://` 列表（IPv4 或域名地址） | v2rayN、Shadowrocket |
| `GET /s/<token>/v2ray-full` | 同上，另含旧节点的 IPv6 字面量链接（与现有 `-full` 等价） | 同上 |
| `GET /s/<token>/clash-split` | Mihomo 完整 profile，分流模式 | Clash Verge Rev、Mihomo Party、FlClash 等 |
| `GET /s/<token>/clash-privacy` | Mihomo 完整 profile，隐私模式 | 同上 |
| `GET /healthz` | 只返回 catalog 是否已加载、版本时间，不含用户数据 | 运维 |

- 未知 token、未知格式、已吊销：统一 404，响应体与耗时不区分原因。
- 响应头：`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Robots-Tag: noindex`、`X-Content-Type-Options: nosniff`；
  用户页面 CSP 禁止外部资源。二维码在服务端生成 SVG（候选 `segno`，纯 Python，版本固定），页面不加载第三方脚本。
- 可选响应头 `profile-update-interval`（Mihomo）与 `Content-Disposition` 文件名不含用户名。
- 没有列举、搜索或管理接口。

### 3.5 渲染与 U5

- **v2ray 格式**：`migrated` 节点输出 Vision 链接，XHTTP 开启时再加一条 XHTTP 链接；地址只用域名（与 canary 一致）。
  `legacy` 节点输出旧链接。节点名 fragment 统一编码为 `<节点>`、`<节点>-xhttp`，不含用户名。
- **Mihomo profile**：以 canary 验证过的隐私 profile 为基础（TUN `mixed` + `strict-route`、`fake-ip` + `respect-rules`、
  `ipv6: true`、sniffer），代理组包含该用户全部节点，XHTTP 使用 `stream-one`。
  - 隐私模式：`MATCH,PROXY`，国内服务也经节点。
  - 分流模式：私有网段与国内域名/IP 直连，其余经节点；规则集来源、版本与更新方式在实现前确定（§5 未知 3）。
  - 没有 IPv6 出口的节点（如 `dzire`）在隐私模式下访问仅 IPv6 目标失败而不回落直连，属预期，用户页面说明。
- **v2rayN、Shadowrocket 的隐私配置**：本阶段只下发 `vless://` 订阅与用户页面说明；这两个客户端的等价路由/规则配置是否能
  以订阅方式下发，按 §5 未知 4 调查后另行决定是否纳入，不阻塞本阶段的其他验收。

### 3.6 访问日志与隐私

- 服务记录：时间、用户名、格式、`CF-Connecting-IP`、User-Agent。**不记录 token 与请求路径**；关闭 uvicorn 自带访问日志
  （路径中含 token）。记录写入服务自有 SQLite，保留 90 天，供 §7.1 migrate 步骤查找长期未拉取订阅的用户。
- 查询接口不对外暴露；运维在 `spt` 本机用 `docker compose exec subs` 运行只读查询命令（运维文档给出）。
- `CF-Connecting-IP` 只用于日志，不参与任何授权判断。
- `[已接受]` Cloudflare 终止 TLS，能看到路径中的 token；与现有 monitor 链路相同，风险由可轮换凭据限定。

### 3.7 部署与暴露

- **镜像**：仓库 `docker/subs/Dockerfile`，基础镜像 `python:3.13-slim` 以 digest 固定，依赖以 `pip --require-hashes` 固定；
  服务代码与渲染器复制进镜像，不在运行时挂载代码。在 `spt` 本机由 Ansible `docker_image_build` 构建，标签为仓库提交号，
  compose 引用该标签并由部署任务核对镜像 ID；不推送镜像仓库。以后需要在其他主机快速部署时再决定是否推送（另行授权）。
- **容器加固**：非 root（UID 10001）、`read_only`、`cap_drop: [ALL]`、`no-new-privileges`、`tmpfs /tmp`、`pids_limit`、内存上限
  128MB、`json-file` 日志上界、`restart: unless-stopped`、`/healthz` 健康检查。`data/` 只读挂载，只有 `db/` 可写。
- **新 playbook `subs.yml`**：
  1. 对 `reality_nodes`（`gather_facts`，只读）计算每节点 ACL、endpoint、IPv6 能力；对 `migrated` 或列入 `edge_nodes` 的节点
     在节点上执行应用器 `verify` 取公钥（只读）；
  2. 在控制端汇总 catalog 与 token 哈希表，运行 schema 校验与渲染 smoke test；
  3. 在 `spt` 构建镜像、渲染 `compose.yaml`、原子替换数据文件，`docker_compose_v2` 使项目处于运行状态。
- **公网暴露**（D1）：选 A 时 `subs` 发布 `127.0.0.1:8100`，由宿主机现有 `cloudflared` 新增公共主机名；选 B 时 compose 内
  `cloudflared` 容器（官方镜像，digest 固定）以独立隧道 token 运行，`subs` 不发布任何宿主机端口。两种方式都不改现有
  `subs.taoziyoyo.com` 与 `monitor.*` 路由。

### 3.8 回滚与退出

| 所处步骤 | 回滚方式 |
|---|---|
| 服务部署（未暴露） | `docker compose down`，删除 `/opt/reality-subs` 与镜像；monitor 与旧订阅不受影响 |
| 公网暴露、仅 `test` | 在 Cloudflare 删除该主机名（选 B 时删除该隧道）；再按上一行删除项目 |
| 已向真实用户发放 | 旧 `subs.taoziyoyo.com` 在 S7 完成前保持可用，用户可换回旧地址；新服务可按用户吊销 |
| 某节点 `migrated` 后出问题 | 把该节点改回 `legacy` 并重新发布 catalog；旧实例在 contract 前仍在运行 |

旧 Gist 与共享 token 的失效（C04）不在本阶段执行：待全部用户已从新地址拉取（以访问日志为证）后，在 S7 中单独授权。

## 4. 范围与非目标

- 范围：`subs.yml`、`subs-remove.yml`、`roles/subs_service`（只渲染 compose 与数据文件、构建镜像、管理 compose 项目）、
  `docker/subs/`、渲染器与服务代码、`tests/subs/`、vault 变量、运维文档；`spt` 上的 compose 项目部署；
  `test` 用户的端到端与真实设备验证。
- 非目标：修改 monitor、`generate_subs_gist.py`、Gist、`/opt/reality/users` 或旧 single/multi；让订阅服务成为控制面
  （签名状态包、agent，属 S2）；grade/features/到期模型（S1/S5）；自检页（S6）；向真实用户发放地址与通知（D2 决定后单独授权）；
  已向真实用户发放新地址后的任何节点 `migrated` 切换（S7 逐台授权）。发放前，canary 节点
  （`dzire`、`usca`、`legend`）可设为 `migrated` 供 `test` 验证，此时新地址没有真实用户。

## 5. 前提、未知与关闭方式

1. **旧链接文件与 ACL 一致**：`/opt/reality/users` 是否只含当前 ACL 允许的节点、是否有已退役节点残留。
   关闭：`subs.yml` 发布前对比两者，差异列出并停止；如有残留，由操作者决定以 ACL 为准过滤。
2. **Cloudflare 隧道新增主机名**：控制台远程管理的隧道可以增加公共主机名并指向 `127.0.0.1:8100`。关闭：D1 决定后由操作者在控制台操作，
   以 `curl https://<新主机名>/healthz` 验证。
3. **分流模式规则集**：国内域名/IP 规则来源（如 Mihomo `geosite:cn`/`geoip:cn` 或规则集 provider）、下载地址是否需要经代理、更新频率。
   关闭：实现前调查上游文档并在本地 `mihomo -t` 与离线启动测试；结论补入本文 §3.5。
4. **v2rayN、Shadowrocket 隐私配置能否经订阅下发**。关闭：查阅两者当前版本文档并在真实设备各测一次；不可行时在用户页面说明限制。
5. **客户端深链接与订阅头**：Clash Verge Rev、Mihomo Party、FlClash 对 `clash://install-config?url=` 的支持，Shadowrocket 与 v2rayN
   对 base64 订阅的解析。关闭：§6.3 真实设备逐项测试，未测项保持缺口。
6. **`spt` 资源**：容器实际内存占用。关闭：本地 compose 运行时测量；`spt` 部署前只读检查可用内存。
7. **镜像构建的网络依赖**：构建需要拉取固定 digest 的基础镜像与 PyPI 包，`spt` 能否直接访问。关闭：本地构建一次；`spt` 构建失败时
   停止并回到本文决定是否改为控制端构建后 `docker save`/`load`。

与假设相反的证据出现时（例如 Cloudflare 无法新增主机名、某客户端无法导入 base64 订阅），停止对应部分并回到本文修订，不以变通方式绕过。

## 6. 验收标准

### 6.1 本地（控制端，无外部写入）

1. 渲染器单元与 golden 测试（synthetic fixtures，无真实值）：`legacy`/`migrated` 混合与切换、XHTTP 开关、无 IPv6 出口节点、
   fragment 与 path 编码、Mihomo 两种模式；`mihomo -t` 与 `xray run -test`（以生成的客户端配置）通过。
2. 凭据与服务测试：A 的 token 取不到 B 的内容；吊销后 404；未知 token 与未知格式响应一致；catalog 或 token 表缺失、损坏、
   schema 不符时 503；token 不出现在服务日志与响应中；发布校验拒绝缺 token、重复 token 与含 `private_key` 的数据。
3. 本地端到端：服务在本机启动，Xray 与 Mihomo 客户端分别从 `v2ray` 与两种 `clash` 地址取配置，连通本地 Xray 服务端访问外网 URL 成功
   （复用 `tests/edge` 的本地服务端）。
4. `subs.yml`、`subs-remove.yml` 与 `roles/subs_service` 语法检查通过；`--list-tasks` 不含旧角色与 monitor 任务；
   `docker compose config` 通过，渲染出的 compose 含 §3.7 全部加固项、没有 Docker socket 挂载。
5. 秘密扫描：仓库、catalog、Ansible 输出中无私钥与 token 明文。
6. 本地以 compose 运行镜像：非 root、根文件系统只读、`data/` 写入失败、健康检查通过；`subs-remove.yml` 等价步骤后无容器、目录与镜像残留。

### 6.2 `spt` 部署（仅 `test`，另行授权）

1. compose 项目 `reality-subs` 运行，镜像 ID 与构建记录一致；选 A 时只发布 `127.0.0.1:8100`，选 B 时不发布端口；`/opt/reality-subs`
   之外没有新增文件、用户或 systemd 单元（部署前后对比 `/etc/systemd/system`、`/etc/passwd` 与宿主机监听端口）；monitor 与宿主机
   `cloudflared` 未变化（服务状态、单元文件哈希）。
2. 本机以 `test` token 取四种格式成功，与控制端 golden 渲染逐字节一致；错误 token 404；访问日志不含 token。
3. 重新发布 catalog 后无需重启即生效；删除 token 表后 503，恢复后正常。
4. 容器重启与 `spt` 上 `docker compose down`/`up -d` 后服务恢复，访问日志保留。

### 6.3 公网与真实设备（仅 `test`，另行授权）

1. 经新主机名：`healthz` 正常；未知 token 404。
2. v2rayN、Shadowrocket：扫描用户页面二维码导入 `v2ray` 订阅，Vision 与 XHTTP 节点可连。
3. Clash 客户端（至少一种）：经深链接或二维码导入 `clash-privacy` 与 `clash-split`；隐私模式下 ipleak 类检测逐项（IPv4、IPv6、
   WebRTC、DNS）只出现节点侧地址；分流模式下行为与页面说明一致（路线图 §7.2 验收）。
4. 把一个 canary 节点从 `migrated` 改为 `legacy` 再改回并重新发布，客户端刷新订阅后该节点在 `test` 的列表中消失又出现。
   此时尚未向真实用户发放新地址，真实用户不受影响。

### 6.4 退出条件

§6.1–6.3 通过，D2 已决定，旧链接一致性（§5 第 1 项）无未解释差异。向真实用户发放与节点 `migrated` 切换不属于本阶段退出条件。

## 7. 风险

- 服务端持有全体用户 UUID：`spt` 同时是邮件主机，本机 root 或 docker 组失陷即暴露；以非 root、只读容器与只读数据挂载限制，
  并在 S7 前评估是否迁到独立主机（D1）。compose 形态使迁移只需复制一个目录。
- 本机构建镜像依赖构建时网络与上游包；以 digest 与 hash 固定输入，构建失败时不回退到浮动标签。
- token 在 URL 中：可能经浏览器历史、截图、Cloudflare 日志外泄；以可轮换、`no-referrer`、`no-store` 与不记录路径限定。
- 旧链接文件为 `0644` 且可能含残留节点：发布前比对 ACL（§5 第 1 项）；旧系统权限按决定 9 不改，S7 contract 后随旧系统删除。
- 分流模式按设计会让国内站点看到真实 IP；页面必须说明，不能宣称“防泄漏”。
- 新旧两个订阅地址并存期间，同一用户可能两个都在用；以访问日志判断，Gist 失效前不删除旧地址。

## 8. 操作者决定

2026-09-15 操作者决定：全部按下列建议执行，D1 暴露方式选 **B**。新隧道的公共主机名尚未给出，在 §6.3 之前由操作者确定。

1. **D1 部署位置与暴露方式**。部署位置建议 `spt`（compose 形态以后可整体搬到独立主机）。暴露方式二选一，主机名由操作者定
   （例如 `sub.<现有域名>`），都不复用 `subs.taoziyoyo.com`，以便新旧并存与独立回滚：
   - **A. 宿主机现有 `cloudflared` 新增主机名**：改动最少；但公网入口不在项目里，搬主机时要另外改隧道。
   - **B. compose 内自带 `cloudflared` 容器与独立隧道**（建议）：整个功能包括入口都在一个目录里，宿主机不开端口，与 monitor 的隧道
     互不影响，搬主机只需复制目录。代价：操作者在 Cloudflare 新建一条隧道并把 token 存入 vault，多维护一个隧道。
2. **D2 订阅地址切换是否与节点迁移同窗通知**（路线图 §9 第 8 项）。建议：**不同窗，先发地址**。本阶段完成后即给用户新地址，
   新地址对未迁移节点输出与旧 Gist 相同的链接，用户体验不变；之后节点逐台切到 443 只需客户端刷新，不再通知。
   这样唯一的手动步骤与迁移风险解耦，访问日志也能先确认谁已换地址。
3. **D3 U5 默认模式**（路线图 §9 第 10 项）。建议：用户页面把**分流模式放在默认位置**，隐私模式并列给出并说明差异。理由：隐私模式让国内
   服务也经海外节点，速度差且更容易触发国内账号风控；两种地址都提供，默认值只决定页面顺序与深链接首选。如更看重“不暴露真实 IP”，
   可改为隐私优先，设计不变。
4. **D4 旧的每用户私钥（C02）**。建议：本阶段不处理。新订阅对旧节点只输出公钥（旧链接本来就不含私钥）；旧私钥随各节点 S7 contract
   删除旧实例而失效，新架构只有节点本地私钥，无需另做全量重签。为缩短旧私钥仍然有效的时间，S7 并行窗口应尽量短。
5. 节点显示名是否沿用现有 `node_alias`（含标签，如 `jp10 [标签]`），还是只用节点名。建议沿用，保持用户已熟悉的名称。

## 9. 授权边界

本文已于 2026-09-15 批准，仅授权工作树内实现与 §6.1 本地验证（含本机构建镜像与本地 compose 运行）。以下各自单独授权：Git 发布；
`spt` 上的镜像构建与 compose 项目部署、变更与删除；Cloudflare 隧道或主机名变更（操作者执行）；隧道 token 写入 vault；镜像推送到任何仓库；vault 中真实用户 token 的生成；向真实用户发放地址或通知；发放后任何节点 `subs_node_states`
改为 `migrated`；旧 Gist、共享 token 与 `subs.taoziyoyo.com` 的失效。

## 10. 已批准的变更

- **2026-09-16 · 页面地址按客户端返回订阅。** 起因：`test` 真机测试时，Shadowrocket 扫到的是页面地址（`/s/<token>`），
  取回的是网页，提示无法获取服务器。操作者批准：页面地址对已知订阅客户端直接返回订阅内容——Shadowrocket、v2rayN、
  v2rayNG、V2Box、Hiddify、Streisand、NekoBox/Nekoray 返回 `v2ray`，Clash、Mihomo、Stash 类返回 `clash-split`（与页面默认模式一致）；
  浏览器与无法识别的客户端仍返回页面。§3.4 的其他地址不变，访问日志记录实际返回的格式。
  影响：用户只需一个二维码；识别依据是 User-Agent，未识别的客户端需使用明确的格式地址。

- **2026-09-17 · 管理控制台接管订阅数据（D-C1～D-C3）。** 起因：2026-09-16 操作者决定新系统作为独立产品建设，
  管理员经控制台发放订阅（[`plan-console-phase1`](../console/plan-console-phase1-2026-09-16.md)，2026-09-17 批准）。操作者批准：
  1. **D-C1**：`catalog.json` 与 `tokens.json` 改由控制台生成并写入 `data/`；`subs.yml` 只负责部署订阅服务。两边只以这两个文件交接，
     订阅服务不增加写入接口、不主动访问控制台，§3.1 的整体搬迁目标保持（搬迁时增加文件传输步骤）。
  2. **D-C2**：新订阅只包含新系统节点，不再输出旧链接；§3.2 的 `legacy` 状态与 `subs_node_states` 随控制台上线停用。
  3. **D-C3**：token 明文由控制台数据库保存（取代 §3.3 的 vault），服务端仍只保存哈希。
  影响：以上三项在控制台第一阶段实施时生效，此前 `subs.yml` 的现行做法不变。§5 第 1 项（旧链接一致性）随 D-C2 不再适用。

- **2026-09-18 · 节点状态页（D-S1、D-S4）。** 起因：操作者要求持有订阅的用户能看到所有节点的当前与历史状态
  （[`plan-node-status-page`](../console/plan-node-status-page-2026-09-18.md)，2026-09-18 批准）。操作者批准：新增 `/s/<token>/status`，
  token 校验与其他地址相同，所有有效 token 看到相同内容；内容来自控制台写入 `data/` 的 `status.json`（D-C1 的文件交接方式不变），
  缺失或无效时显示“暂无状态数据”，不影响订阅内容；用户页增加链接。状态页不写访问日志，不计入“最后拉取”。
  影响：§3.4 增加一个地址；页面不含脚本与节点连接信息。
