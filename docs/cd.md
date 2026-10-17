# CD：Actions 构建，服务器主动拉取

生产入口：**https://www.myzyitzj.cn:19443/resume/**。公网 TCP 19443 映射到宿主机 TCP 443。

```text
手动运行 Build approved release（dev 上明确的提交）
    → 离线测试、前端构建
    → 在 GitHub runner 构建 Linux amd64 后端镜像
    → Redis + 完整后端容器启动冒烟，不调用付费模型
    → 发布带提交 SHA、校验和的 GitHub Release
服务器 systemd timer（每两分钟，通过本机代理主动访问 GitHub）
    → 核对发布工作流已成功、提交/分支/事件/工作流路径一致
    → 下载并校验 GitHub asset digest + manifest SHA-256
    → 本机证书与公网 HTTPS 映射检查、部署锁、SQLite 在线备份
    → 加载镜像、更新独立 Compose 项目、健康检查
    → 先放静态资源，再原子切换 index.html
    → 失败恢复旧容器和旧 index，不自动倒灌数据库
```

服务器不跑构建，也不运行具有生产权限的 GitHub self-hosted runner。**不依赖 GitHub runner SSH 入站**。旧只读 SSH 工作流保留用于诊断，不参与发布。

## 发布

1. 将审核后的代码推送 `dev`；普通 push/PR 只跑 CI，不部署。
2. Actions → **Build approved release** → Run workflow → 选择 `dev`。确认选择的是准备上线的提交。
3. 工作流会生成 `resume-<完整提交SHA>` 的 Release，发布包不含 `.env`、数据库、日志或访问密钥。
4. 服务器在发布工作流最终成功后自动部署，通常下一次轮询开始处理；下载和启动还需要时间。**Actions 绿色表示产物已发布，不代表服务器已经部署成功。**
5. 核对线上 `/resume/api/health` 的 `version` 与目标 SHA，以及实际页面和登录。

同一个 SHA 的 Release 不覆盖、不重复发布。失败后先排查，修复代码并产生新提交再发布，不通过删除校验或替换已有产物绕过检查。

GitHub 仓库是公开的，服务器无需 PAT。公开 Release 也会公开可分发的应用镜像：不得将秘密写进镜像或静态文件；如果以后仓库改私有，需要重新设计最小权限的下载凭证。

## 生产目录与隔离

| 路径 | 用途 |
| --- | --- |
| `/opt/apps/resume/compose.yaml` | 固定的运行配置；不从 Release 执行脚本 |
| `/opt/apps/resume/shared/backend.env` | 生产密钥，root-only；来自原环境配置，移除外部认证项，Redis 使用已有 Docker 网络 |
| `/opt/apps/resume/shared/logs/` | 后端日志 |
| `/opt/apps/resume/current.json` | 当前版本、上一版元数据和部署时间 |
| `/opt/apps/resume/releases/<SHA>/` | SQLite 安全备份、旧 index 和版本记录 |
| `/opt/apps/resume/failed.json` | 失败版本；阻止同一版本不断重试 |
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
curl https://www.myzyitzj.cn:19443/resume/api/health

# 暂停后续发布，不停止正在服务的应用
systemctl stop resume-deploy.timer
# 检查无正在运行的更新，排查后再恢复
systemctl enable --now resume-deploy.timer
```

首次启用定时器前应完成公网 HTTPS、Compose、环境与数据库挂载检查；安装参考本仓库 `deploy/` 和固定脚本 `scripts/cd/pull-release.py`。部署脚本更新需要维护者审核后通过现有可信 SSH 安装，不让下载包任意替换 root 脚本。

发布失败时会尝试恢复旧容器的原运行状态及旧页面入口，并留下 `FAILED` 与 `failed.json`。如果当前应用原来就已停止，首次发布失败也不会擅自把旧版启动上线。更早的 SQLite 备份保留用于人工恢复，**不会自动恢复数据库而丢弃新写入**。当前邮箱账号升级为加表/加列的兼容变更；未来不兼容迁移必须先制定专门的回滚方案。

版本目录保留最近三次成功发布的记录与备份；清理仅针对本项目记录的旧镜像，不触碰其他项目。静态旧哈希资源暂不自动删除，需观察磁盘；失败版本也保留供排查，不能无限堆积。

当前自动化的是**部署失败回退**，不是任意历史版本的一键回滚。已成功上线后的人工回退，应先暂停 timer，核对数据库兼容性、目标镜像和旧 index；不要简单改 `current.json` 假装回滚完成。

## 代理

发布 service 显式使用宿主机 `127.0.0.1:7890`，本地流量不走代理。没有配置系统全局代理或 Docker daemon 代理：镜像是在 Actions 构建并作为归档下载、`docker load` 导入，因此生产不需要 `docker pull`。见 [server-proxy.md](server-proxy.md)。

旧代理订阅的备份和诊断副本已按要求删除，当前使用的配置仍保留；**这不包括部署前必须保留的 SQLite 备份**。

## 第二个项目

这套“可信发布包 + 固定拉取器 + 独立 Compose/数据 + 统一 TLS 入口”的模式可以复用，但当前脚本明确绑定简历项目，尚未抽象为通用平台。第二个项目接入时给它独立发布标记、目录、锁和健康检查，验证后再提取公共代码；不要直接共享数据库或用同一 Compose 项目覆盖服务。
