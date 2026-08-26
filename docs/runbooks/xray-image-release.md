# Xray 镜像发布操作手册

最后复核：2026-08-26 JST

## 1. 用途与资料边界

本文用于构建、发布、验证、晋升和回滚
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
镜像存储把同一个顶层 digest 绑定到两个不同平台。每个通道生成：

- Xray 版本标签，例如 `v26.3.27`；
- `build-<Git-SHA>-xray-<Xray版本>`；
- 验证成功后才移动对应的浮动标签。

版本标签和 build 标签会保留，但 Docker 标签本身可变。部署和证据应使用
不可变的顶层 digest。构建与定时晋升工作流共用
`xray-image-alias-update` 并发组，避免同时修改浮动标签。

当前工作流没有单通道参数。无论由 `ops` push 还是手动触发，都会同时构建
stable 和 prerelease。

## 3. 操作边界

- **只读操作：** 本地校验、Git 检查、官方 release 查询、Docker Hub 标签和
  manifest 查询。
- **Git 写操作：** 暂存、提交、推送、创建 PR 和合并；执行前必须审阅准确路径、
  分支和提交范围。
- **Docker Hub 写操作：** 合入 `ops` 后的自动构建、手动构建、定时晋升、
  `docker push` 或移动标签的 `imagetools create`。
- **不属于本阶段发布：** 修改 VPS/Ansible 镜像引用、部署节点、DNS、Gist 或订阅。

不要把 Docker Hub Token 放入 Git、命令行参数、工作流日志、证据或聊天。
不要用 `.github/workflows/test.yml` 发布正式镜像；它只会写入无关的
`taoziyoyo2566/dockerhub-test:test` 测试镜像。

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

stable 构建在移动 `stable/latest` 前，会解析旧 `latest` 的 digest，并对它执行
和目标镜像相同的运行时验证。

只读检查：

```bash
curl --fail --silent --show-error --location \
  https://hub.docker.com/v2/repositories/taoziyoyo2566/xray_docker/tags/latest \
  | jq '{name, digest, platforms: [.images[] | select(.os == "linux") | (.os + "/" + .architecture)]}'
```

如果 `latest` 不存在、digest 格式错误，或者没有同时包含 `linux/amd64` 和
`linux/arm64`，立即停止。不得临时删除回滚门禁；应先单独评审并建立可验证的
bootstrap/rollback 镜像。

## 5. 每次发布前检查

在仓库根目录执行：

```bash
git status --short --branch

bash -n \
  docker-build/check-inputs.sh \
  docker-build/verify-image.sh \
  tests/test_xray_image_verify.sh

bash docker-build/check-inputs.sh
bash docker-build/check-inputs.sh \
  docker-build/XRAY_PRERELEASE_VERSION \
  docker-build/XRAY_PRERELEASE_SHA256SUMS

bash tests/test_xray_image_verify.sh
git diff --check
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
- prerelease pin 的 `prerelease=true`、`draft=false`；
- 两组版本/校验和通过本地验证。

正式工作流还会把仓库 SHA256 与 GitHub 官方 asset digest 比对。不要为了让构建
通过而删除这层远端校验。

## 6. 发布代码变更

第一阶段初始 PR 已经合入 `ops`。不要重复使用已经合并的
`feat/xray-modernization`；修复或版本升级必须从当前 `origin/ops` 创建新的、
已评审分支。当前验证器修复分支是 `fix/xray-image-verifier`。

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

分支必须是本次已评审分支，仓库本地身份查询必须无输出。以下是当前验证器修复的
候选路径；执行前以实际 diff 为准，禁止用 `git add .` 或 `git add -A`：

```bash
git add -- \
  docker-build/verify-image.sh \
  tests/test_xray_image_verify.sh \
  docs/project-memory.md \
  docs/reviews/roadmap-xray-xhttp-ipv6-2026-08-25.md \
  docs/reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md \
  docs/runbooks/xray-image-release.md

git diff --cached --check
git diff --cached --stat
git status --short --branch
```

完整审阅暂存 diff 后，只使用本次发布评审中批准的 commit 和 push 命令。不得从
本文复制一个旧提交消息来替代当次评审。

```bash
git diff --cached
```

如果认证、hook 或远端状态拒绝操作，停止并重新评审。不要自动 amend、跳过 hook、
force push、pull、rebase 或用不同命令重试。

## 7. 创建 PR 并触发构建

创建 base=`ops`、head=本次功能分支的 PR。当前验证器修复对应：

`https://github.com/taoziyoyo2566/reality-ops/compare/ops...fix/xray-image-verifier?expand=1`

合并前确认：

1. `Repository quality checks` 全部通过；
2. 两个 Docker Hub Repository Secrets 已存在；
3. 已完整审阅相对 `ops` 的分支 diff；
4. 没有无关路径或 secret。

合并操作摘要：

- **发生什么：** 把已评审 PR 合入默认分支 `ops`。
- **预期影响：** `push` 事件启动 `Build and Push Xray Images`，stable 和
  prerelease job 都会写 Docker Hub。
- **Registry 范围：** 两个通道的版本/build 标签，以及验证后的
  `stable/latest/prerelease`。
- **风险与恢复：** 一个通道可能成功而另一个失败；即使浮动标签未移动，版本/build
  标签也可能已经存在。原有版本标签不会删除，浮动标签按第 11 节回滚。
- **不包含：** 不部署 VPS，不修改 Ansible 镜像引用。

使用仓库正常的非 force PR merge。合并本身就是构建触发器，不要紧接着重复手动
运行同一工作流。

## 8. 观察并验收构建

进入 `GitHub → Actions → Build and Push Xray Images`，检查 stable 和
prerelease 两个 matrix job。每个通道必须通过：

1. `Validate pinned release`；
2. `Build and push image`；
3. `Verify pushed multi-platform image`；
4. stable 还必须通过 `Verify rollback image`；
5. `Promote verified channel aliases`。

验证器要求镜像恰好包含 `linux/amd64` 和 `linux/arm64`，从顶层 index 取出
每个平台的子 manifest digest，分别执行 `/usr/bin/xray -version`，并核对预期
版本。不得对两个平台重复运行同一个顶层 digest。

不能把 push step 成功当成发布成功。如果验证失败，版本/build 标签可能已经推送，
但对应浮动标签不应移动。

当前首次发布完成后必须满足：

| 标签 | 必须满足 |
|---|---|
| `v26.3.27`、`stable`、`latest` | 顶层 digest 相同 |
| `v26.7.28`、`prerelease` | 顶层 digest 相同 |
| 两个版本 digest | 都包含 `linux/amd64`、`linux/arm64` |

只读查询：

```bash
for image_tag in v26.3.27 stable latest v26.7.28 prerelease; do
  curl --fail --silent --show-error --location \
    "https://hub.docker.com/v2/repositories/taoziyoyo2566/xray_docker/tags/${image_tag}" \
    | jq -r '[.name, .digest, ([.images[] | select(.os == "linux") | .architecture] | sort | join(","))] | @tsv'
done
```

把工作流 URL、`ops` commit、版本标签、目标 digest、已验证架构/版本、旧回滚
digest 和所有缺口写入第一阶段证据。不得记录 Token 或完整凭据输出。

## 9. 手动重新构建

只有 `build-image.yml` 已存在于默认分支后，GitHub 才允许
`workflow_dispatch`。当前手动工作流也会同时重建两个通道。

操作摘要：

- **发生什么：** 在 `ops` 手动运行 `Build and Push Xray Images`。
- **预期影响：** 两个通道重新构建并推送；通过验证的浮动标签可能移动。
- **风险：** 不是 dry-run，且不能只选择一个通道。
- **不包含：** 不部署 VPS。

进入：

`Actions → Build and Push Xray Images → Run workflow → Branch: ops`

不要运行 DockerHub Login Test。按第 8 节观察和验收。

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

执行第 5 节检查，通过 PR 合入 `ops`，再按第 7–8 节发布。由于当前 workflow
会构建两个通道，未修改的通道也会被重新构建和验证。

### prerelease 变为 stable

定时工作流 `Promote Stable Xray Image` 查询官方 latest stable，并在 Docker Hub
查找相同版本标签：

- 如果该版本之前已作为 prerelease 构建，工作流验证目标和旧 `latest` 后，直接
  移动 `stable/latest`，不重新构建；
- 如果版本标签不存在，晋升会安全失败，必须先走正常构建；
- 自动晋升不会修改仓库里的 `XRAY_VERSION` 和校验和。晋升后应提交普通 PR，
  让 stable pin 与官方当前 stable 同步。

## 11. 回滚 stable/latest

只能使用成功构建 summary 和第一阶段证据中记录的、已经验证过的旧 digest。
不得从浮动标签猜测回滚目标。

回滚操作摘要：

- **发生什么：** 只把 `stable` 和 `latest` 指回一个已验证的旧顶层 digest。
- **目标：** `taoziyoyo2566/xray_docker`；版本/build 标签保持不变。
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

## 12. 故障处理

| 故障 | 安全解释与下一步 |
|---|---|
| stable pin 不是官方 latest | 更新 stable 版本和校验和；不要绕过验证。 |
| release digest 不一致 | 仓库校验和过期或错误；重新读取官方 assets。 |
| Docker Hub 登录失败 | 检查 Secret 名、Token 期限和 Read & Write 权限；轮换 Token，不打印它。 |
| 目标验证失败 | 版本/build 标签可能已存在，但浮动标签不应移动；先诊断 manifest/二进制。 |
| `docker: cannot overwrite digest` | 运行的是重复使用顶层 digest 的旧验证器；合入子 manifest digest 修复后触发新 run，不要重跑旧 run。 |
| 回滚镜像验证失败 | stable 浮动标签不应移动；先建立已验证回滚镜像。 |
| 当前 `latest` 不存在 | stable 构建会有意失败；单独评审 bootstrap，不得临时删门禁。 |
| 定时晋升找不到版本标签 | 先构建该官方版本；晋升工作流从不负责构建。 |
| stable 成功、prerelease 失败，或反过来 | 分通道检查真实标签/digest，只处理失败通道的原因。 |
| 工作流一直排队 | 检查 `xray-image-alias-update` 并发组，不要重复启动。 |
| VPS 仍运行旧镜像 | 镜像发布不会部署节点；另行评审部署和 digest 选择。 |

## 13. 官方参考

- Docker Hub Personal Access Token：
  <https://docs.docker.com/security/access-tokens/>
- GitHub Actions Repository Secrets：
  <https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets>
- GitHub Actions 手动运行工作流：
  <https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow>
