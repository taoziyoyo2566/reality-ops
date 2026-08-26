# Xray 镜像发布操作手册

最后复核：2026-08-26 JST

## 1. 用途与资料边界

本文用于构建、发布、验证、版本发现和回滚
`taoziyoyo2566/xray_docker` 镜像，是第一阶段的可执行操作入口。

其他资料各自保留单一职责：

- [现代化路线图](../reviews/roadmap-xray-xhttp-ipv6-2026-08-25.md)：阶段目标、范围和验收条件；
- [第一阶段证据](../reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md)：已执行结果、digest 和未完成缺口；
- 本文：操作命令、停止条件、故障处理和回滚。

## 2. 发布模型

| 通道 | 版本文件 | 校验和文件 | 浮动标签 |
|---|---|---|---|
| stable | `docker-build/XRAY_VERSION` | `docker-build/XRAY_SHA256SUMS` | `stable`、`latest` |
| prerelease | `docker-build/XRAY_PRERELEASE_VERSION` | `docker-build/XRAY_PRERELEASE_SHA256SUMS` | `prerelease` |

构建工作流同时发布 `linux/amd64` 和 `linux/arm64`。验证器先从顶层 index
解析两个平台各自的子 manifest digest，再分别运行子 digest，避免 Docker 本地
镜像存储把同一个顶层 digest 绑定到两个不同平台。每个通道按以下顺序发布：

- 先把候选内容按 digest 推入 registry，不创建公开标签；
- 使用候选 digest 完成双架构运行时验证；
- 验证成功后才创建 Xray 版本标签（例如 `v26.3.27`）和对应浮动标签。

stable 版本标签使用上游版本号，例如 `v26.3.27`；prerelease 版本标签追加
`-prerelease`，例如 `v26.7.28-prerelease`。这样同一上游版本后来成为正式版时，最终
重新构建的 `v26.7.28` 不会与旧预发布镜像混淆。

工作流不发布 `build-*` 标签。版本标签和浮动标签都可变，发布证据和运行时验证
应使用不可变的顶层 digest。当前 Ansible 部署默认仍使用
`taoziyoyo2566/xray_docker:latest`，还没有改为 digest pin。构建与标签修复工作流共用
`xray-image-alias-update` 并发组，避免同时修改浮动标签；定时 stable 检查是只读的。

发布相关工作流各自只负责一种动作：

| 工作流 | 作用 | 是否构建 | 是否写 registry |
|---|---|---|---|
| `Build and Push Xray Images` | 新版本发布或显式重建 | 是 | 是 |
| `Repair Xray Image Tags` | 从已有 digest 修复版本/渠道标签 | 否 | 是 |
| `Check Upstream Stable Xray Release` | 发现 stable 版本或资产漂移 | 否 | 否 |
| `Audit Xray Image Tags` | 每周盘点保留、垃圾、未知和缺失标签 | 否 | 否 |

手动重建必须选择 `stable`、`prerelease` 或 `all`。版本输入合入 `ops` 时只构建
发生变化的通道；Dockerfile 变化才构建两个通道。工作流和辅助脚本本身的修改不会
自动写 registry，合入后需按本手册选择通道运行。

每次 stable 更新前会验证回滚候选，成功发布时将旧 digest 保存为
`stable-previous`。候选解析顺序是 `latest`、`stable`、`stable-previous`，最后才是
`docker-build/XRAY_IMAGE_KEEP_TAGS` 中明确保留的 bootstrap 标签。

## 3. 操作边界

- **只读操作：** 本地校验、Git 检查、官方 release 查询、Docker Hub 标签和
  manifest 查询。
- **Git 写操作：** 暂存、提交、推送、创建 PR 和合并；执行前必须审阅准确路径、
  分支和提交范围。
- **Docker Hub 写操作：** 合入 `ops` 后的自动构建、手动构建、标签修复、
  `docker push` 或移动标签的 `imagetools create`。
- **不属于本阶段发布：** 修改 VPS/Ansible 镜像引用、部署节点、DNS、Gist 或订阅。

不要把 Docker Hub Token 放入 Git、命令行参数、工作流日志、证据或聊天。旧的
`DockerHub Login Test` 会写入无关的 `dockerhub-test:test`，现已从发布设计中删除；
不要再用推送临时镜像的方式测试登录。

## 4. 一次性准备

### 4.1 配置 Docker Hub 凭据

操作摘要：

- **发生什么：** 创建 Docker Hub Personal Access Token，并添加两个 GitHub
  Repository Secrets。
- **目标：** Docker Hub 账户 `taoziyoyo2566` 和 GitHub 仓库
  `taoziyoyo2566/reality-ops`。
- **预期影响：** GitHub Actions 可以写入 `taoziyoyo2566/xray_docker`。
- **风险与恢复：** Token 泄露可导致未授权推送；只授予 Read & Write、设置期限，
  暴露后立即撤销并轮换。
- **不包含：** 此步骤不会构建镜像、移动标签、合并代码或部署节点。

在 Docker Hub 创建只有 **Read & Write** 权限的 Token，不要授予 Delete。
然后进入 GitHub 仓库：

`Settings → Secrets and variables → Actions → New repository secret`

添加：

| Secret | 值 |
|---|---|
| `DOCKERHUB_USERNAME` | `taoziyoyo2566` |
| `DOCKERHUB_TOKEN` | Docker Hub Personal Access Token |

### 4.2 确认首次发布的回滚候选

stable 构建在移动 `stable/latest` 前，会解析并验证一个回滚 digest。正常情况下
使用旧 `latest`；如果渠道标签缺失，则按第 2 节的候选顺序回退。

只读检查：

```bash
curl --fail --silent --show-error --location \
  https://hub.docker.com/v2/repositories/taoziyoyo2566/xray_docker/tags/latest \
  | jq '{name, digest, platforms: [.images[] | select(.os == "linux") | (.os + "/" + .architecture)]}'
```

如果 `latest` 不存在，执行只读解析器确认是否存在其他明确保留的候选：

```bash
bash docker-build/resolve-rollback-image.sh taoziyoyo2566/xray_docker
```

解析出的 digest 仍必须同时包含 `linux/amd64` 和 `linux/arm64` 并通过运行时验证。
没有候选时立即停止；不得临时删除回滚门禁。

## 5. 每次发布前检查

在仓库根目录执行：

```bash
git status --short --branch

bash -n \
  docker-build/check-inputs.sh \
  docker-build/check-release-state.sh \
  docker-build/select-image-channels.sh \
  docker-build/resolve-image-repair.sh \
  docker-build/resolve-rollback-image.sh \
  docker-build/audit-image-tags.sh \
  docker-build/verify-image.sh \
  tests/test_xray_release_state.sh \
  tests/test_xray_image_channel_selection.sh \
  tests/test_xray_image_repair.sh \
  tests/test_xray_rollback_resolution.sh \
  tests/test_xray_image_tag_audit.sh \
  tests/test_xray_image_workflow.sh \
  tests/test_xray_image_verify.sh

bash docker-build/check-inputs.sh
bash docker-build/check-inputs.sh \
  docker-build/XRAY_PRERELEASE_VERSION \
  docker-build/XRAY_PRERELEASE_SHA256SUMS

bash tests/test_xray_release_state.sh
bash tests/test_xray_image_channel_selection.sh
bash tests/test_xray_image_repair.sh
bash tests/test_xray_rollback_resolution.sh
bash tests/test_xray_image_tag_audit.sh
bash tests/test_xray_image_workflow.sh
bash tests/test_xray_image_verify.sh
git diff --check
git diff --cached --check
```

查询官方通道状态：

```bash
stable_version="$(tr -d '[:space:]' < docker-build/XRAY_VERSION)"
prerelease_version="$(tr -d '[:space:]' < docker-build/XRAY_PRERELEASE_VERSION)"

curl --fail --silent --show-error --location \
  https://api.github.com/repos/XTLS/Xray-core/releases/latest \
  | jq -r '.tag_name'

curl --fail --silent --show-error --location \
  "https://api.github.com/repos/XTLS/Xray-core/releases/tags/${stable_version}" \
  | jq '{tag_name, prerelease, draft}'

curl --fail --silent --show-error --location \
  "https://api.github.com/repos/XTLS/Xray-core/releases/tags/${prerelease_version}" \
  | jq '{tag_name, prerelease, draft}'
```

必须满足：

- stable pin 等于官方 latest，且 `prerelease=false`、`draft=false`；
- prerelease pin 必须同时满足 `prerelease=true`、`draft=false`；如果已转为
  stable，预发布构建必须失败并要求更新 pin，不能把旧镜像改名为正式版；
- 两组版本/校验和通过本地验证。

正式工作流还会把仓库 SHA256 与 GitHub 官方 asset digest 比对。不要为了让构建
通过而删除这层远端校验。

## 6. 发布代码变更

第一阶段初始实现和验证器修复都已经合入 `ops`。不要重复使用已经合并的
`feat/xray-modernization` 或 `fix/xray-image-verifier`；每次修复或版本升级都必须
从当前 `origin/ops` 创建新的、已评审分支。

操作摘要：

- **发生什么：** 提交本次已评审的修复或版本输入，并把当前功能分支推送到
  `origin`。
- **目标：** 仅任务明确列出的文件和对应远端功能分支，PR base 保持 `ops`。
- **预期影响：** 可以创建 PR 并运行质量检查；只推送非 `ops` 分支不会构建镜像。
- **风险与恢复：** 不使用 force；若路径、身份、分支或 diff 与评审不一致则停止。
- **不包含：** 不合并、不写 Docker Hub、不部署 VPS、不删除分支。

先确认身份和范围：

```bash
git branch --show-current
git config --global user.name
git config --global user.email
git config --local --get user.name || true
git config --local --get user.email || true
git status --short --branch
```

分支必须是本次已评审分支，仓库本地身份查询必须无输出。先检查实际变更：

```bash
git diff --check
git diff --cached --check
git diff --stat
git diff --cached --stat
git diff --name-status
git diff --cached --name-status
git diff
git diff --cached
git status --short --branch
```

之后必须针对当次实际路径形成单独评审的精确 `git add`、commit、push 和 PR
操作包。禁止使用 `git add .`、`git add -A`，也不要从本文复制旧路径或旧提交消息
代替当次评审。

如果认证、hook 或远端状态拒绝操作，停止并重新评审。不要自动 amend、跳过 hook、
force push、pull、rebase 或用不同命令重试。

## 7. 创建 PR 并在需要时触发构建

创建 base=`ops`、head=本次功能分支的 PR。不要复用已合并 PR 的 head 分支。

合并前确认：

1. `Repository quality checks` 全部通过；
2. 两个 Docker Hub Repository Secrets 已存在；
3. 已完整审阅相对 `ops` 的分支 diff；
4. 没有无关路径或 secret。

合并操作摘要：

- **发生什么：** 把已评审 PR 合入默认分支 `ops`。
- **预期影响：** stable 版本/校验和变化只构建 stable，prerelease
  版本/校验和变化只构建 prerelease，Dockerfile 变化才构建两者。工作流、辅助脚本、
  测试和文档变化不会在合并时自动写 Docker Hub。
- **Registry 范围：** 工作流被触发时，先写入无公开标签的候选 digest；验证成功后
  才创建该通道的版本标签和 `stable/latest/prerelease`。
- **风险与恢复：** 一个通道可能成功而另一个失败。失败通道不会创建公开标签，但
  registry 可能暂存不可见的无标签内容并按服务端策略回收；成功通道按第 12 节回滚。
- **不包含：** 不部署 VPS，不修改 Ansible 镜像引用。

使用仓库正常的非 force PR merge。镜像相关路径的合并本身就是构建触发器，不要
紧接着重复手动运行同一工作流。

## 8. 观察并验收构建

进入 `GitHub → Actions → Build and Push Xray Images`，检查 stable 和
prerelease 两个 matrix job。每个通道必须通过：

1. `Validate pinned release`；
2. `Build and push image`；
3. `Verify pushed multi-platform image`；
4. stable 还必须通过 `Verify rollback image`；
5. `Preserve rollback and publish verified tags`。

验证器要求镜像恰好包含 `linux/amd64` 和 `linux/arm64`，从顶层 index 取出
每个平台的子 manifest digest，分别执行 `/usr/bin/xray -version`，并核对预期
版本。不得对两个平台重复运行同一个顶层 digest。

不能把 push step 成功当成发布成功。如果验证失败，候选 digest 可能已经进入
registry，但版本标签和浮动标签都不应创建或移动。

每次发布完成后必须满足：

| 标签 | 必须满足 |
|---|---|
| stable 版本标签、`stable`、`latest` | 顶层 digest 相同 |
| prerelease 版本标签、`prerelease` | 顶层 digest 相同 |
| 两个版本 digest | 都包含 `linux/amd64`、`linux/arm64` |

只读查询：

```bash
stable_version="$(tr -d '[:space:]' < docker-build/XRAY_VERSION)"
prerelease_version="$(tr -d '[:space:]' < docker-build/XRAY_PRERELEASE_VERSION)"

for image_tag in "${stable_version}" stable latest "${prerelease_version}-prerelease" prerelease; do
  curl --fail --silent --show-error --location \
    "https://hub.docker.com/v2/repositories/taoziyoyo2566/xray_docker/tags/${image_tag}" \
    | jq -r '[.name, .digest, ([.images[] | select(.os == "linux") | .architecture] | sort | join(","))] | @tsv'
done
```

2026-08-26 已验证基线：

| 标签 | 顶层 digest | 已验证运行时 |
|---|---|---|
| `v26.3.27`、`stable`、`latest` | `sha256:5b905e8ff49804690109f74e305611869513a803d5bacf9d1f24d5fa4b1e40ce` | `v26.3.27`，`linux/amd64,linux/arm64` |
| `v26.7.28`、`prerelease`（旧机制的历史标签） | `sha256:53cb9d8730738744a2dbe8c73502e5cd1d8667b14012fbd38a4a38e13495c3f8` | `v26.7.28`，`linux/amd64,linux/arm64` |

stable 晋升前还验证了旧 `latest`
`sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`，
其双架构运行时版本为 `v25.12.8`。完整证据见
[第一阶段证据](../reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md)。

把工作流 URL、`ops` commit、版本标签、目标 digest、已验证架构/版本、旧回滚
digest 和所有缺口写入第一阶段证据。不得记录 Token 或完整凭据输出。

## 9. 手动重新构建

只有 `build-image.yml` 已存在于默认分支后，GitHub 才允许
`workflow_dispatch`。手动运行必须明确选择通道。

操作摘要：

- **发生什么：** 在 `ops` 手动运行 `Build and Push Xray Images`，选择
  `stable`、`prerelease` 或 `all`。
- **预期影响：** 只重建所选通道；通过验证后才更新该通道标签。
- **风险：** 不是 dry-run；`all` 会写两个通道，普通恢复不要选择它。
- **不包含：** 不部署 VPS。

进入：

`Actions → Build and Push Xray Images → Run workflow → Branch: ops → channel`

按第 8 节观察和验收。

当前恢复状态（2026-08-26 JST）：Docker Hub 只剩回滚 SHA、`v26.7.28` 和
`prerelease`；`v26.3.27/stable/latest` 已删除，原 stable digest 也不可再按 digest
读取。因此本次流程代码合入后应只选择 `stable` 重建；不得选择 `all`，也不需要
重建仍完整的 prerelease。在恢复前不要新建节点或强制拉取默认的 `latest`。

stable 恢复完成后，单独运行一次 `Repair Xray Image Tags`，选择 `prerelease` 且
`source_digest` 留空。它会从现有 `prerelease` digest 验证并补建
`v26.7.28-prerelease`，不重建镜像。旧机制留下的无后缀 `v26.7.28` 会由审计列为
人工判断项；确认新后缀标签和浮动标签 digest 一致后，再单独评审删除旧标签。

## 10. 更新 stable 或 prerelease

从最新 `origin/ops` 创建功能分支。选定官方版本后，查询 release 状态和 asset
digest：

```bash
xray_release_version='vX.Y.Z'
release_json="$(curl --fail --silent --show-error --location \
  "https://api.github.com/repos/XTLS/Xray-core/releases/tags/${xray_release_version}")"

jq '{tag_name, prerelease, draft}' <<< "${release_json}"
jq -r '
  .assets[]
  | select(.name == "Xray-linux-64.zip" or .name == "Xray-linux-arm64-v8a.zip")
  | "\(.digest | sub("^sha256:"; ""))  \(.name)"
' <<< "${release_json}" | sort
```

检查输出后，只更新一个通道对应的一对文件：

- stable：`XRAY_VERSION`、`XRAY_SHA256SUMS`；
- prerelease：`XRAY_PRERELEASE_VERSION`、`XRAY_PRERELEASE_SHA256SUMS`。

执行第 5 节检查，通过 PR 合入 `ops`，再按第 7–8 节发布。输入文件变化只触发其
所属通道，未修改的通道不会重建。

### prerelease 变为 stable

定时工作流 `Check Upstream Stable Xray Release` 只查询官方 latest stable，并比较
仓库中的 stable 版本和两个资产 digest。它不登录 Docker Hub、不移动标签，也不复用
已有 prerelease 镜像。

发现新 stable 或同版本资产 digest 变化时，检查会失败并提示操作员：

1. 从最终 release API 重新读取版本和 amd64/arm64 资产 digest；
2. 通过普通 PR 更新 `XRAY_VERSION` 和 `XRAY_SHA256SUMS`；
3. 合入 `ops` 后由 stable 通道重新下载最终资产、重新构建并完整验证；
4. 验证成功后发布 `vX.Y.Z/stable/latest`；
5. 将 prerelease pin 更新为新的官方预发布版。没有新预发布版时，不要重建旧 pin。

即使上游最终版复用相同版本号，也必须重新构建。旧预发布版本标签是
`vX.Y.Z-prerelease`，最终正式版本标签是 `vX.Y.Z`，两者可以保留不同 digest。

## 11. 修复缺失或错误标签

`Repair Xray Image Tags` 只重新指向已有 digest，不构建镜像。选择一个通道后，流程
依次尝试使用版本标签、通道标签，以及 stable 的 `latest` 作为源；stable 版本标签为
`vX.Y.Z`，prerelease 版本标签为 `vX.Y.Z-prerelease`。源 digest 必须
通过双架构和 Xray 版本验证才会写标签。

适用场景：

- 误删 `v26.3.27`，但 `stable` 或 `latest` 仍存在；
- 误删渠道标签，但版本标签仍存在；
- 已知、可读取且有证据的 digest 仍存在，可把它作为可选 `source_digest` 输入。

如果所有源标签均已删除且原 digest 返回 `not found`，修复流程必须失败；此时只能按
第 9 节显式重建相应通道。`source_digest` 不能绕过验证，也不能用于猜测已被垃圾
回收的内容。

标签修复与构建共用并发组。stable 修复还会验证被替换的回滚候选，并在需要
时保存为 `stable-previous`。

## 12. 回滚 stable/latest

优先使用 `stable-previous`。也可以使用成功构建 summary、第一阶段证据和
`XRAY_IMAGE_KEEP_TAGS` 中记录的、已经验证过的旧 digest。不得从浮动标签猜测
回滚目标。

回滚操作摘要：

- **发生什么：** 只把 `stable` 和 `latest` 指回一个已验证的旧顶层 digest。
- **目标：** `taoziyoyo2566/xray_docker`；版本标签保持不变。
- **预期影响：** 使用 `stable/latest` 的消费者回到旧镜像；digest-pinned 消费者不变。
- **风险与恢复：** 错误 digest 会同时错误移动两个标签；执行前核对证据，执行后查询
  两个标签。
- **不包含：** 不删除镜像、不修改 Git、不 force、不部署 VPS。

在 Docker Buildx 可用的主机上交互登录，Token 只在密码提示时输入：

```bash
docker login --username taoziyoyo2566
```

设置已评审 digest 并检查 manifest：

```bash
rollback_digest='sha256:<verified-64-hex-digest>'
rollback_ref="taoziyoyo2566/xray_docker@${rollback_digest}"

docker buildx imagetools inspect "${rollback_ref}"
```

移动两个浮动标签：

```bash
docker buildx imagetools create \
  --tag taoziyoyo2566/xray_docker:stable \
  --tag taoziyoyo2566/xray_docker:latest \
  "${rollback_ref}"
```

重新执行第 8 节只读查询，并记录原因、操作者、时间、旧/新 digest 映射和验证结果。
节点部署回滚是另一个操作，不由本次标签回滚授权。

## 13. 标签保留与清理

公开标签只保留：

- 每个需要保留的 stable 标签 `vX.Y.Z` 和 prerelease 标签
  `vX.Y.Z-prerelease`；
- `stable`、`latest`、`prerelease`；
- `stable-previous` 以及必要的 bootstrap 回滚引用。

不保留 `build-*` 标签、失败候选标签或没有消费者和回滚证据的旧 Git SHA 标签。
多个标签指向同一 digest 不会复制整套镜像层；删除标签只删除引用，共享 digest
仍被其他标签引用时镜像继续存在。最后一个标签删除后，底层内容可能由 Docker Hub
垃圾回收，不能把恢复能力当作保证。

清理前必须：

1. 重新读取完整标签、digest 和更新时间；
2. 搜索仓库、部署变量和已知运行节点是否引用候选标签；
3. 明确列出保留与删除集合；
4. 单独评审 Docker Hub 删除操作；
5. 删除后重新读取标签并验证保留标签的 digest。

本地或 GitHub Actions 运行以下只读审计：

```bash
bash docker-build/audit-image-tags.sh
```

`Audit Xray Image Tags` 每周运行并报告四组内容：保留、清理候选、需要人工判断、缺失
的必要标签。它不会登录 Docker Hub，也不会删除任何标签。合规且不与当前
prerelease 身份冲突的版本标签会保留；
`build-*` 和未列入 `XRAY_IMAGE_KEEP_TAGS` 的 40 位 Git SHA 才自动归入清理候选。
如果当前 prerelease 版本仍存在无后缀的 `vX.Y.Z` 标签且它不是 stable pin，该标签
会进入人工判断，以便迁移到 `vX.Y.Z-prerelease` 后单独清理。
存在任一清理候选、未知标签或必要标签缺失时，审计 job 失败以发出信号，但仍不执行
删除。

旧登录测试还留下独立仓库 `taoziyoyo2566/dockerhub-test:test`。正式流程不再引用它；
删除该测试仓库是单独的 Docker Hub 破坏性操作，不能与 `xray_docker` 标签清理共用
一个模糊授权。

GitHub Actions 使用的发布 Token 只应具有 Read & Write 权限。人工清理由 Docker Hub
账户界面执行，不为日常构建 Token 增加 Delete 权限，也不把凭据写入命令或资料。

## 14. 故障处理

| 故障 | 安全解释与下一步 |
|---|---|
| stable pin 不是官方 latest | 更新 stable 版本和校验和；不要绕过验证。 |
| release digest 不一致 | 仓库校验和过期或错误；重新读取官方 assets。 |
| Docker Hub 登录失败 | 检查 Secret 名、Token 期限和 Read & Write 权限；轮换 Token，不打印它。 |
| 目标验证失败 | 候选 digest 可能已写入，但不应存在对应公开标签；先诊断 manifest/二进制。 |
| prerelease pin 已变为 stable | prerelease 构建必须失败；从最终官方资产更新 stable pin 并 fresh build，同时选择新的 prerelease pin。 |
| `docker: cannot overwrite digest` | 运行的是重复使用顶层 digest 的旧验证器；合入子 manifest digest 修复后触发新 run，不要重跑旧 run。 |
| 回滚镜像验证失败 | stable 浮动标签不应移动；先建立已验证回滚镜像。 |
| 当前 `latest` 不存在 | 解析 `stable/stable-previous/XRAY_IMAGE_KEEP_TAGS`；只有候选也不存在或验证失败时才停止。 |
| 版本标签被误删 | 仍有可读取源 digest 时运行标签修复；源 digest 已 `not found` 时只重建相应通道。 |
| 审计报告缺失必要标签 | 先判断能否无重建修复；不要用 `all` 重建未受影响通道。 |
| stable 检查报告版本或资产漂移 | 更新 stable 版本和最终资产校验和，通过 PR 触发 fresh build；不得复用 prerelease 镜像。 |
| stable 成功、prerelease 失败，或反过来 | 分通道检查真实标签/digest，只处理失败通道的原因。 |
| 工作流一直排队 | 检查 `xray-image-alias-update` 并发组，不要重复启动。 |
| VPS 仍运行旧镜像 | 镜像发布不会部署节点；另行评审部署和 digest 选择。 |

## 15. 官方参考

- Docker Hub Personal Access Token：
  <https://docs.docker.com/security/access-tokens/>
- GitHub Actions Repository Secrets：
  <https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets>
- GitHub Actions 手动运行工作流：
  <https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow>
- Docker image/registry exporter（包含 `push-by-digest`）：
  <https://docs.docker.com/build/exporters/image-registry/>
- Docker Hub 标签查看与删除：
  <https://docs.docker.com/docker-hub/repos/manage/hub-images/tags/>
