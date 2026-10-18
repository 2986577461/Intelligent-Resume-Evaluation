# CD：Actions 构建，服务器主动拉取

生产入口：**https://www.myzyitzj.cn:19443/resume/**。公网 TCP 19443 映射到宿主机 TCP 443。

```text
手动运行 Build approved release（main 上明确的提交）
    → 离线测试、前端构建
    → 在 GitHub runner 构建 Linux amd64 后端镜像
    → 隔离网络中的 Redis + 后端 + HTTPS 网关业务冒烟，不调用付费模型
    → 发布带提交 SHA、校验和的 GitHub Release
服务器 systemd timer（每两分钟，通过本机代理主动访问 GitHub）
    → 核对发布工作流已成功、提交/main 分支/手动事件/工作流路径一致
    → 分段断点下载，并校验 GitHub asset digest + manifest SHA-256
    → 本机证书与公网 HTTPS 映射检查、部署锁、SQLite 在线备份
    → 加载镜像、更新独立 Compose 项目、健康检查
    → 先放静态资源，再原子切换 index.html
    → HTTPS 校验页面和静态资源的哈希、API 版本、未登录访问保护
    → 失败尝试恢复旧容器和旧 index，不自动倒灌数据库
    → 写入部署状态，可选 webhook 通知（重复故障去重）
```

服务器不跑构建，也不运行具有生产权限的 GitHub self-hosted runner。**不依赖 GitHub runner SSH 入站**。旧只读 SSH 工作流保留用于诊断，不参与发布。

## 发布

1. 日常代码提交到 `dev`，准备发布时创建 **`dev` → `main`** 的 PR；CI 通过并审核后合并。普通 push/PR（包括 `main` 合并后的 push）只跑 CI，不部署。
2. Actions → **Build approved release** → Run workflow → 选择 **`main`**。确认分支当前提交正是准备上线的版本；工作流会使用触发时确定的 SHA，之后的提交不会改变本次产物。选择其他分支会跳过构建。
3. 工作流会生成 `resume-<完整提交SHA>` 的 Release，发布包不含 `.env`、数据库、日志或访问密钥。
4. 服务器在发布工作流最终成功后自动部署，通常下一次轮询开始处理；下载和启动还需要时间。大镜像分为 2 MiB 分段，最多六路下载，失败保留私有缓存供下次继续，全部完成后仍校验完整 SHA-256。**Actions 绿色表示产物已发布，不代表服务器已经部署成功。**
5. 查看服务器 `deployment-status.json` 的结果；启用 webhook 后也会收到通知。核对线上 `/resume/api/health` 的 `version` 与目标 SHA，以及实际页面和登录。

同一个 SHA 的 Release 不覆盖、不重复发布。失败后先排查，修复代码并产生新提交再发布，不通过删除校验或替换已有产物绕过检查。

GitHub 仓库是公开的，服务器无需 PAT。公开 Release 也会公开可分发的应用镜像：不得将秘密写进镜像或静态文件；如果以后仓库改私有，需要重新设计最小权限的下载凭证。

## 在 VS Code 中发版

安装 GitHub 官方 **GitHub Actions** 扩展并登录后，可以在扩展侧边栏找到 `Build approved release`，手动运行并选择 `main`。也可以直接打开仓库的 Actions 页面，按上面的步骤操作。

VS Code 终端使用 GitHub CLI 时，先安装 `gh` 并完成 `gh auth login`，再执行：

```bash
# 先确认 dev → main 的 PR 已合并，再发布 main；不要用 --ref dev。
gh workflow run release.yml --ref main
# 查看这次发布构建的运行结果（不等于服务器部署结果）
gh run list --workflow release.yml --branch main --event workflow_dispatch --limit 5
```

## 分支规则与首次切换

目标流程是 **`dev` 持续开发 → PR/CI → `main` → 手动发布 → 自动部署**。合并 `main` 仅表示版本可发布，不会直接上线。CI 保留对所有 push/PR 的检查；只有生产发布来源改为 `main`。

管理员需要在 GitHub 为 `main` 配置 PR 合并和三个必过 CI 检查，限制绕过保护的直接 push；多人维护时可另外配置生产 Environment 审批。本次代码改动不会自动开启这些仓库设置。默认分支使用 `main`，并确保它包含 `release.yml`，否则 GitHub 可能不显示手动运行入口。**修改默认分支不会合并代码、更新服务器或触发发布。**

GitHub 修改默认分支后，在本地同步远端默认指向即可；不需要把日常开发分支切换为 `main`：

```bash
git fetch origin --no-tags
git remote set-head origin -a
git symbolic-ref refs/remotes/origin/HEAD
# 应输出 refs/remotes/origin/main；当前工作分支仍可保持 dev。

# 检查远端 main 是否已有发布工作流；无输出表示还需要先合并代码。
git ls-tree -r --name-only origin/main -- .github/workflows/release.yml
```

默认分支已经是 `main`，但 Actions 没有手动发布入口时，先检查上面的文件，不要为了显示按钮把 CD 改回 `dev`。

首次从旧的 `dev` 发布切到 `main` 时，按此顺序操作：

1. 先将本地变更提交并推送到 `dev`，再创建 `dev` → `main` 的 PR。CI 通过并审核后合并，确认远端 `main` 已包含 `.github/workflows/release.yml`；暂不点击发布。
2. 在服务器暂停 deploy timer，等待正在执行的 deploy service 自然结束；核对当前 SHA，没有未处理的旧发布。
3. 审核并安装本版固定拉取脚本和 service（见下方“安装本次基础设施更新”），再恢复 timer。旧脚本只信任 `dev`，新脚本只信任 `main`，不能只改工作流。
4. 手动选择 `main` 发布一个新 SHA，检查服务器部署状态和线上健康版本。

已部署的历史 `dev` 版本不会被主动停止或重新部署；旧 Release 不删除、不替换，也不从 `dev` 再发布新版本。若最新旧 Release 尚未部署，需先人工确认处理，不要绕过新脚本的来源校验。该脚本对已部署 SHA 会直接返回，因此安装新脚本本身不会重启业务。

## 生产目录与隔离

| 路径 | 用途 |
| --- | --- |
| `/opt/apps/resume/compose.yaml` | 固定的运行配置；不从 Release 执行脚本 |
| `/opt/apps/resume/shared/backend.env` | 生产密钥，root-only；来自原环境配置，移除外部认证项，Redis 使用已有 Docker 网络 |
| `/opt/apps/resume/shared/logs/` | 后端日志 |
| `/opt/apps/resume/current.json` | 当前版本、上一版元数据和部署时间 |
| `/opt/apps/resume/releases/<SHA>/` | SQLite 安全备份、旧 index 和版本记录 |
| `/opt/apps/resume/failed.json` | 部署失败或校验不合法的版本；阻止同一版本不断重试 |
| `/opt/apps/resume/deployment-status.json` | 最新尝试的阶段、时间、结果、恢复状态与安全诊断 |
| `/opt/apps/resume/notification.json` | 最近成功发送的通知去重标识，不含 webhook 地址 |
| `/opt/apps/resume/shared/deploy-notify.env` | 可选通知配置，root-only，不进入应用容器 |
| `/root/resume/data/conversation-db/` | **保留原路径**：账号、会话、附件 SQLite |
| `/root/resume/data/agent-db/` | **保留原路径**：Agent checkpoint SQLite |
| `/root/resume/frontend/` | 保留原静态 bind mount 的目录 inode；旧哈希资源保留给已打开的页面 |

仅操作 `resume-backend`、独立 `resume-https` 和本项目版本目录，不执行全局 `compose down`、Docker prune 或 Docker daemon 重启。Redis、MySQL、Java 和原 Nginx 容器保持原样。

后端只暴露 Docker 内网端口，不映射到宿主公网；运行时限制内存/CPU、只读镜像文件系统、关闭 capabilities，SQLite/日志单独挂载。为兼容原 root 所有的数据目录，后端暂未切换非 root 用户；权限迁移应单独验证。

## HTTPS 与续期

- 免费 Let's Encrypt 证书，使用 acme.sh + 阿里云 DNS 验证，与公网是否有 443 无关。
- 独立 `resume-https` 容器监听宿主 443，证书和配置见 `deploy/tls/`。
- TLS 网关用 `$http_host` 保留 `:19443`，防止同源登录校验失败；SSE 关闭缓冲。
- 生产 Cookie 设置 `Secure`，后端拒绝来自旧 HTTP 代理的账号请求；发布版前端会跳转固定 HTTPS 入口。
- `resume-acme.timer` 每天检查续期，成功安装证书后校验配置并仅 reload 新 TLS 容器；不重启原 Nginx。
- DNS 凭证、ACME 账号与日志只在 root 目录，不输出到 CI，也不进 Git。

```bash
systemctl list-timers resume-acme.timer
# 测试续期检查（未临近到期时不会强制申请新证书）
systemctl start resume-acme.service
openssl x509 -in /opt/apps/resume/tls/live/fullchain.pem -noout -dates
```

阿里云 RAM 凭证必须长期有效且仅有必要的 DNS 权限。轮换凭证后检查续期；不要直接移除 RAM 用户或把账号交给应用容器。

## 观察、暂停与故障处理

```bash
systemctl status resume-deploy.timer --no-pager
systemctl status resume-deploy.service --no-pager
journalctl -u resume-deploy.service --since today --no-pager
cat /opt/apps/resume/current.json
cat /opt/apps/resume/deployment-status.json
curl https://www.myzyitzj.cn:19443/resume/api/health

# 暂停后续发布，不停止正在服务的应用
systemctl stop resume-deploy.timer
# 检查无正在运行的更新，排查后再恢复
systemctl enable --now resume-deploy.timer
```

首次启用定时器前应完成公网 HTTPS、Compose、环境与数据库挂载检查；安装参考本仓库 `deploy/` 和固定脚本 `scripts/cd/pull-release.py`。部署脚本更新需要维护者审核后通过现有可信 SSH 安装，不让下载包任意替换 root 脚本。

发布失败时会尝试恢复旧容器的原运行状态及旧页面入口，并留下 `FAILED` 与 `failed.json`。有已记录的旧版且原来在运行时，还会检查恢复后的后端健康和 HTTPS 版本；恢复失败会明确标记 `rollback: failed`，不能把“执行了回退”当作“恢复成功”。如果当前应用原来就已停止，首次发布失败也不会擅自把旧版启动上线。更早的 SQLite 备份保留用于人工恢复，**不会自动恢复数据库而丢弃新写入**。当前邮箱账号升级为加表/加列的兼容变更；未来不兼容迁移必须先制定专门的回滚方案。

版本目录保留最近三次成功发布的记录与备份；清理仅针对本项目记录的旧镜像，不触碰其他项目。静态旧哈希资源暂不自动删除，需观察磁盘；失败版本也保留供排查，不能无限堆积。

当前自动化的是**部署失败回退**，不是任意历史版本的一键回滚。已成功上线后的人工回退，应先暂停 timer，核对数据库兼容性、目标镜像和旧 index；不要简单改 `current.json` 假装回滚完成。

## 部署状态和通知

`deployment-status.json` 记录 SHA、Release 链接、阶段、起止时间、耗时、`status`、`retryable`、`rollback` 和通知结果。阶段包括来源校验、下载、预检查、加载镜像、备份、启动、前端切换、网关冒烟和清理。

- `status: succeeded`：新容器和生产网关只读检查已通过。`cleanup: failed` 表示发布正常但旧资源清理需要处理。
- `status: failed, retryable: true`：尚未进入被禁止重试的部署失败状态，例如网络下载或部署前 HTTPS 检查失败；下次 timer 会重试。
- `status: failed, retryable: false`：版本已写入 `failed.json`，需要人工排查。包结构/校验不合法也会阻止该 SHA 自动重试。
- `status: waiting`：Release 已发布，但其 Actions 工作流尚未最终完成，下轮再检查，不发失败通知。
- `rollback: verified`：旧版健康和 HTTPS 版本验证通过；`state_restored` 只表示恢复了旧容器的运行/停止状态，没有已记录版本可完成上述验证；`failed` 需要人工介入。
- 已上线或被阻止的同一 SHA 不重复部署，也不覆盖上次结果；被阻止版本仍返回非零退出码，让 systemd 保持故障可见，不重复发送相同通知。`running` 长时间不结束可能是进程中断或超时，需结合 systemd 状态排查；当前没有独立的失联告警。

生产网关冒烟会逐一比较 `index.html` 与所有 `assets/` 静态资源（含懒加载模块）的 SHA-256，防止旧页面、缺失资源被 SPA fallback 掩盖；同时确认匿名访问账号/会话接口返回 401。全程只读，不创建生产账号，不调用模型。它不是浏览器交互测试。

### 可选 webhook

默认不启用，不影响部署。在服务器创建 root 所有、权限 `0600` 的 `/opt/apps/resume/shared/deploy-notify.env`，通过编辑器写入（不要把真实地址写进 Git 或命令历史）：

```ini
RESUME_DEPLOY_WEBHOOK_URL=https://your-notification-relay.example/deployment
```

systemd service 通过可选 `EnvironmentFile` 读取。Webhook 是**通用 JSON POST**，接收端需理解下列格式；不是直接兼容企业微信/飞书等机器人的专有消息格式，可以由通知中转服务适配。

```json
{
  "event": "resume.deployment",
  "schema": 1,
  "sha": "<full commit SHA>",
  "status": "failed",
  "phase": "start_backend",
  "retryable": false,
  "rollback": "verified",
  "error_type": "RuntimeError",
  "release_url": "https://github.com/2986577461/Intelligent-Resume-Evaluation/releases/tag/resume-<SHA>"
}
```

实际负载还包含时间、耗时、稳定的 `error_code` 和允许输出的诊断字段。只允许 HTTPS、不跟随重定向，单次 10 秒超时；不发送异常原文、容器环境变量、原始日志、Cookie、简历内容或 webhook 地址。故障诊断只包含容器运行/健康状态、退出码、OOM 等白名单字段；新容器在删除前保存失败状态。

连续相同 SHA/阶段/错误类别及错误码/恢复结果的通知在成功发送后去重；通知失败不改变应用部署结果，下次轮询重试。当前是单条最近结果的尽力通知，不是持久消息队列；新部署覆盖旧结果前应关注通知故障。HTTP 2xx 只说明接收端接受请求，具体消息投递由接收端保证。

### 安装本次基础设施更新

**仅发布新应用不会安装这批服务器侧功能。** 维护者需单独审核并通过可信 SSH 分发以下文件：

- `scripts/cd/pull-release.py` → `/usr/local/libexec/resume-pull-release`
- `deploy/resume/resume-deploy.service` → `/etc/systemd/system/resume-deploy.service`

先停止 timer，确认当前 deploy service 已结束，不要杀掉正在进行的部署；备份旧脚本和 service，再安装上述文件并执行 `systemctl daemon-reload`。配置通知文件后，重新启用 `resume-deploy.timer`。无需重启业务容器，也无需修改数据库或现有 Compose/TLS 配置。

仓库侧可先执行离线回归；完整验收需在隔离测试环境模拟下载失败、启动失败、回退失败和通知失败，核对状态、去重及恢复结果。不要为了验收故意破坏生产版本。本轮没有配置 GitHub 分支保护/Environment 审批，也没有实现任意历史版本的一键回滚。

## 代理

发布 service 显式使用宿主机 `127.0.0.1:7890`，本地流量不走代理。没有配置系统全局代理或 Docker daemon 代理：镜像是在 Actions 构建并作为归档下载、`docker load` 导入，因此生产不需要 `docker pull`。见 [server-proxy.md](server-proxy.md)。

旧代理订阅的备份和诊断副本已按要求删除，当前使用的配置仍保留；**这不包括部署前必须保留的 SQLite 备份**。

## 第二个项目

这套“可信发布包 + 固定拉取器 + 独立 Compose/数据 + 统一 TLS 入口”的模式可以复用，但当前脚本明确绑定简历项目，尚未抽象为通用平台。第二个项目接入时给它独立发布标记、目录、锁和健康检查，验证后再提取公共代码；不要直接共享数据库或用同一 Compose 项目覆盖服务。

## 首次验收记录（2026-10-18，北京时间）

- 应用版本：`6a5584ed6dfbb3e703ec88226907e0a110ccbcd4`；[发布工作流成功](https://github.com/2986577461/Intelligent-Resume-Evaluation/actions/runs/39052468210)，Release 为 `resume-6a5584ed6dfbb3e703ec88226907e0a110ccbcd4`。
- 后端镜像在 runner 构建并通过 Redis + 完整应用启动测试；线上容器状态 `healthy`，HTTPS `/resume/api/health` 返回同一个 SHA。未调用付费模型接口。
- 实际验证邮箱注册、Secure/HttpOnly/SameSite Cookie、会话恢复、退出撤销和再次登录；临时测试账号和会话已清理。原 HTTP 代理的账号请求被拒绝。
- 浏览器实测正式 HTTPS 页面的静态资源、登录弹窗及线上版本检查通过。
- 初次切换被 Redis 鉴权失败拦截：原环境凭证与当前共享 Redis 不匹配。自动恢复了旧容器的停止状态和旧 index；同步新后端配置后，人工确认重试，同一个不可变发布包部署成功。未修改共享 Redis，也未恢复/覆盖数据库。
- 这次失败尝试及其 SQLite 备份保存在 `/opt/apps/resume/failed-releases/`，区别于已经按要求删除的旧代理配置备份，不做自动删除。
- 原数据保留：1 个会话、4 条消息、6 个 Agent checkpoints，两个 SQLite 完整性检查均为 `ok`；没有把旧数据认领给新邮箱账号。
- 再次轮询返回 `Already deployed`，没有重复重建应用；部署 timer 和证书续期 timer 已启用。
- 当前安装的 Compose 为官方 `v5.6.0` 插件，未重启 Docker daemon。原 Nginx、Java、Redis、MySQL 容器没有重启。
