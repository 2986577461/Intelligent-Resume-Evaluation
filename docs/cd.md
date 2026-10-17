# CD：先验证 GitHub 到服务器的连接

目标是 **GitHub Actions 构建产物，再自动通过 SSH 上传到 NAT 服务器**。服务器不需要访问 GitHub/GHCR，也不承担构建；后续其他项目可以复用验证过的部署步骤。这里的 SSH 上传是流水线自动执行，不是每次发布都手动上传。

**当前只实现第一步：手动触发的只读连接检查，尚未实现自动发布。** 不会修改 Nginx、安装 Compose、更新容器、访问业务数据库或读取 `.env`，也不会影响现有 CI。

## 当前实例配置

- GitHub 的 `deployment-check` 环境已创建，仅允许 `dev` 分支使用；五项凭证已配置为 Environment secrets。
- 服务器新增 `/usr/local/libexec/resume-connection-probe`，为 root 所有且仅 root 可写。
- `/root/.ssh/authorized_keys` 新增专用探测公钥，使用下文的 `restrict` 和固定命令；原有密钥保留，修改前已在原目录备份。
- 本机使用专用密钥检查通过；另外请求执行无关命令，确认服务器仍只执行固定探测脚本。
- 未安装软件，未修改 Nginx、业务配置或容器。GitHub runner 的实际结果以 Actions 运行记录为准。

这些是当前服务器的一次性接入记录；下面保留新环境接入方法，不要重复覆盖已有配置。

## 1. 一次性配置连接凭证

仓库 Settings → Environments 中创建 `deployment-check`：

- Deployment branches and tags 只允许维护者审核过的分支，当前工作分支为 `dev`。不要允许任意分支使用凭证。
- 如果仓库套餐支持，配置 Required reviewers，并禁止自行批准。
- 添加下面五个 **Environment secrets**，不要写入仓库、Issue 或工作流日志。

| Secret | 内容 |
| --- | --- |
| `CD_SSH_HOST` | 公网可达的 IP 或域名，不能填写仅本机可用的 `ithome` SSH 别名 |
| `CD_SSH_PORT` | NAT 映射的公网 SSH 端口，不是默认假定的 22 |
| `CD_SSH_USER` | 服务器上允许连接的账号 |
| `CD_SSH_PRIVATE_KEY` | 专用于此检查的无口令私钥，包含 BEGIN/END 的完整多行内容 |
| `CD_SSH_KNOWN_HOSTS` | 已通过可信渠道核对的服务器公钥记录，非 22 端口形如 `[公网主机]:端口 ssh-ed25519 AAAA...` |

### 专用密钥，不复用日常 root 私钥

在可信电脑上生成，放在仓库外：

```bash
ssh-keygen -t ed25519 -N '' \
  -C 'github-actions-resume-connection-check' \
  -f "$HOME/.ssh/resume-actions-check"
```

私钥存入上述 Secret，不进入仓库；本地原件也应保存在仓库外并限制权限。把 `.pub` 公钥交给服务器管理员配置。为了只验证网络，可用独立的普通账号，不过该账号还需要获准查询 Docker；加入 docker 组本质上具有接近 root 的权限，不应当成普通低权限访问。

现阶段更稳妥的做法是：管理员将仓库中的 `scripts/cd/remote-probe.sh` 审核后放到服务器上一个仅 root 可写的位置，如 `/usr/local/libexec/resume-connection-probe`，再给专用公钥配置固定命令：

```text
restrict,command="/bin/bash /usr/local/libexec/resume-connection-probe" ssh-ed25519 <专用公钥内容> github-actions-resume-connection-check
```

固定命令忽略客户端提供的命令，只做只读探测；`restrict` 关闭转发、PTY 等能力。脚本及其父目录不得由受限账号写入。**不要把 `restrict` 单独当成命令限制，它本身不阻止执行任意远端命令。** 后续发布另行配置经过限制的部署入口，不直接放开这个探测密钥。

### 核对服务器指纹

通过现有可信 SSH 连接或服务商控制台查看服务器的公钥及指纹，例如：

```bash
ssh -o StrictHostKeyChecking=yes root@ithome \
  'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub; cat /etc/ssh/ssh_host_ed25519_key.pub'
```

这里只读取 **公钥**，不能读取不带 `.pub` 的主机私钥。用核实过的公钥组成 `CD_SSH_KNOWN_HOSTS` 记录，其中地址和端口必须匹配 Actions 使用的公网地址。也可以使用经过核对的 hashed known_hosts 记录。

不要在 Actions 中临时 `ssh-keyscan` 然后直接信任，也不要设置 `StrictHostKeyChecking=no`。更换主机密钥时，重新核实后更新 Secret。

## 2. 手动执行

将工作流提交到 GitHub 后，在 Actions → **Check deployment connection** → Run workflow，选择已审核的分支。

当前仓库默认分支是 `dev`，推送到 `dev` 后即可手动触发。其他仓库需要注意：GitHub 的 `workflow_dispatch` 要求工作流已存在于仓库的**默认分支**。如果只推到 `dev`，默认分支还没有此文件，页面可能没有运行按钮。应通过审核把连接检查相关文件合入默认分支，再选择 `dev` 运行；不要为了出现按钮就未经审核合并整个 `dev`，也不要临时改成 push/PR 触发带生产凭证的任务。

通过标准：

- GitHub-hosted runner 用专用密钥完成公网 SSH 认证；
- 服务器主机公钥匹配预先核实的 known_hosts；
- 远端可以访问 Docker daemon，并具备 `tar`、`gzip`、`sha256sum`、`flock`；
- 日志出现 `SSH_PROBE_OK`，Actions summary 记录只读检查通过。

只有旧版 `docker-compose` 时会给出警告，不会安装或更新它。SSH 检查通过**不等于已经可以上线**，还没有验证 SCP、大文件传输、镜像导入、HTTPS、应用健康检查和回滚。

## 故障定位

| 现象 | 优先检查 |
| --- | --- |
| `missing CD_SSH_...` | Environment 名称和 Secret 名称是否一致，当前分支是否获准使用该环境 |
| 超时 / Connection refused | NAT 端口映射、防火墙、云安全组，以及公网 SSH 是否只允许固定来源；GitHub-hosted runner 的出口不是本机出口 |
| Host key verification failed | Secret 中主机/端口、公钥记录是否正确；先核实，不能绕过检查 |
| Permission denied (publickey) | 用户、公钥安装位置、权限和对应的专用私钥；不要把私钥打印到日志排查 |
| 无法连接 Docker daemon | Docker 是否运行、执行账号是否有查询权限；不要通过 `chmod 666 /var/run/docker.sock` 解决 |

脚本可以在可信本地环境中复现：安全地设置这五个环境变量，然后运行 `bash scripts/cd/check-ssh.sh`。不要开启 `set -x`；脚本会在临时目录写入权限受限的凭证文件，退出时清理。

脚本离线回归测试（只用 Python 标准库，SSH 被替身替换，不读取真实凭证）：

```bash
python3 -m unittest discover -s scripts/cd -p 'test_*.py' -v
```

这组测试也纳入普通 CI，不需要服务器 Secrets。

## 后续发布边界

连接通过后再接：CI 成功的明确提交 → 构建前后端 → 带 SHA 和校验和的版本包 → 自动上传 → 部署锁、SQLite 安全备份 → 加载镜像、Compose 更新 → 健康检查 → 失败恢复上一版镜像。

- PR 和普通 `dev` 推送仍只运行 CI。生产先采用手动选择受信任版本，批准后自动完成部署。
- 服务器安装 Compose v2、配置部署入口、调整 Nginx、实际切换服务，都属于后续实施，不在此连接检查中执行。
- 保留现有数据库挂载和其他业务容器；镜像回滚不能替代数据库迁移方案。
- 先让简历项目跑通完整发布，再抽取公共步骤接第二个项目；不在生产机安装 Jenkins 或运行构建用的 self-hosted runner。

## 撤销这次探测权限

不再需要时，先禁用连接检查工作流，再从 GitHub 删除 `deployment-check` 环境中的五项 Secrets；服务器只删除 `authorized_keys` 中注释为 `github-actions-resume-connection-check` 的这条专用公钥，不要覆盖整个文件或恢复旧备份，以免丢掉后来添加的其他密钥。确认不再引用后可删除固定探测脚本，以及本机仓库外的专用密钥。日常 SSH 密钥不受影响。
