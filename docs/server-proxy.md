# 服务器出站代理

此代理用于解决服务器访问 GitHub 等部分外网地址超时，不用于开放入站 SSH。**不能直接修复 GitHub-hosted runner 连接 NAT 公网 SSH 端口超时的问题。**

## 当前状态：新订阅验证通过，代理运行中

2026-10-18（北京时间）切换至新订阅，选用 4 个逐一实测 GitHub API 返回 HTTP 200 的 VLESS 节点。运行配置仅包含这 4 个已验证节点，通过延迟检测选择出口。

最终经代理验证：GitHub 和 GitHub API 均为 HTTP 200，GHCR 与 Docker Hub 的 `/v2/` 均返回预期的 HTTP 401（说明 registry 的 TLS/HTTP 入口可达，但不代表已完成认证或镜像拉取）。切换后的 GitHub 请求复验也通过。

`resume-proxy.service` **正在运行并已启用开机启动**，仍只监听 `127.0.0.1:7890`。未配置系统全局代理，也未给 Docker daemon 或业务容器配置代理；默认路由、Nginx 和现有容器不变。

新订阅无法从服务器直连下载，本次在可信本机通过 HTTPS 下载完整快照后，使用现有可信 SSH 上传。运行配置由 URI 列表裁剪生成，保留 TLS 校验和 Reality 参数；19 个带证书固定指纹的 Hysteria2 URI 暂未导入，未通过丢弃证书固定指纹来绕过转换问题。

旧代理配置备份与旧订阅诊断副本已按要求删除，不再保留 `backup-before-yfjc-*` 或测试前的旧配置。当前新订阅、转换配置及测试记录仍位于 root 私有目录 `candidate-yfjc/`，不得上传仓库。

## 历史排查：旧订阅未通过，服务曾停止

2026-10-17 实测：订阅通过 HTTPS 证书校验下载，配置语法检查通过；代理进程成功以动态用户运行，仅监听 `127.0.0.1:7890`。但经代理访问 GitHub、GitHub API、GHCR、Docker Hub 均未取得有效 HTTP 响应；对订阅内 61 个不同 TCP 节点端点逐个测试，全部连接超时。

主机 iptables OUTPUT 策略为 ACCEPT、无其他 OUTPUT 规则。尚不能确定是服务商出站链路、节点侧限制，还是其他网络原因；不能仅凭超时归因于订阅凭证错误，也不能推断所有 UDP 节点都不可达。

**当时代理已停止且未启用开机启动；后续已按上方记录换订阅并验证通过。** 现有容器未修改或重启。安装程序、订阅快照和诊断记录保留在 root 专用目录，临时安装包已清理。

## 历史排查：旧订阅的 Shadowrocket 链接

同日按用户提供的 Shadowrocket 参数重新下载订阅，HTTPS 校验通过。返回内容为 base64 编码的 URI 列表，解析出 51 个节点（43 AnyTLS、5 Hysteria2、3 Trojan）、48 个不同端点；这些端点全部存在于原 Mihomo 订阅，不是新的连接入口。

- 将 13 个节点主机的系统解析与阿里公共 DoH 解析对照，全部有共同 IPv4 地址；使用公共 DNS 的 IPv4 再测 43 个 TCP 端点，仍全部超时。因此目前没有证据表明单纯修改 DNS 可以解决。
- 临时生成仅包含 Hysteria2 的配置，再逐一选中 5 个 UDP 节点访问 GitHub API，全部未获得有效 HTTP 响应；脱敏日志显示 `context deadline exceeded`。这能确认这些测试失败，但不能据此断言所有 UDP 流量被封锁。
- 补测结束已恢复原配置、停止代理；仍未启用开机启动。未修改防火墙、默认路由、Docker 或其他业务服务。
- 手机能使用同一订阅，并不意味着云服务器所在网络到节点也可达。下一步应核对服务商的出站限制和节点是否限制数据中心来源；目前无法确定具体哪一层拦截。

## 配置边界

- 客户端：官方 MetaCubeX/Mihomo `v1.19.32`，下载包对照 GitHub Release API 公布的 SHA-256 校验，传输后再次校验。
- 服务：`resume-proxy.service`，systemd 动态用户运行，使用权限隔离和只读系统目录；配置通过 systemd credentials 提供，不以 root 运行业务进程。
- 监听：仅 `127.0.0.1:7890`，兼容 HTTP 和 SOCKS5；不开管理接口、不开放局域网访问、不启用 TUN 或代理 DNS，不修改默认路由。
- 订阅只提取支持的节点，生成全新配置；不导入订阅的控制接口、远程规则或提供者。节点证书校验开启。
- 节点组自动探测延迟并选择可用节点，检测地址为 Google 的 `generate_204`，每 10 分钟检查一次。探测会产生少量流量。
- 只有明确配置代理的程序会走代理；现有 Nginx、Redis、其他容器、Docker daemon 都没有改动。

## 使用

在服务器上为单条命令显式指定代理：

```bash
curl --proxy http://127.0.0.1:7890 --connect-timeout 10 --max-time 30 -I https://github.com
```

为某个 shell 中后续工具启用（退出 shell 后失效）：

```bash
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export ALL_PROXY=socks5h://127.0.0.1:7890
export NO_PROXY=localhost,127.0.0.1,::1
```

`HTTPS_PROXY` 的值使用 `http://` 是正常的：客户端用 HTTP CONNECT 建立到 HTTPS 目标的隧道，目标站点的 TLS 仍由客户端验证。不要为解决连通问题关闭 TLS 校验。

这里的 `127.0.0.1` 是宿主机：容器内的同名地址指向容器自己，不能照搬。给 Docker daemon 配置代理通常涉及重启 Docker，需另行评估现有业务，当前未配置。

## 运维与撤销

```bash
systemctl status resume-proxy --no-pager
systemctl is-enabled resume-proxy
ss -ltn 'sport = :7890'

# 关闭代理并取消开机启动，不会重启业务容器
systemctl disable --now resume-proxy
```

关闭代理前，应先取消使用方的代理环境变量，否则这些程序会连接失败。重新启用：

```bash
systemctl enable --now resume-proxy
```

## 凭证与订阅更新

服务器文件仅供管理员读取：

| 位置 | 内容 |
| --- | --- |
| `/root/.config/resume-proxy/subscription-url` | 原始订阅链接，权限 600 |
| `/root/.config/resume-proxy/subscription.raw` | 订阅快照，含节点凭证，权限 600 |
| `/etc/resume-proxy/config.yaml` | 经过裁剪的运行配置，权限 600 |
| `/etc/resume-proxy/release.json` | 安装版本与校验值 |
| `/etc/resume-proxy/verification.json` | 最近一次网络验证结果，不含凭证 |
| `/usr/local/bin/resume-mihomo` | 官方客户端二进制 |

**目前使用订阅快照，没有定时刷新订阅；本次服务器无法直接下载新订阅。** 订阅过期或节点变化时，需要通过可信 SSH 更新链接，校验 HTTPS 下载、重新生成并验证裁剪后的配置，再只重启 `resume-proxy`。不要直接用原始订阅覆盖运行配置，否则可能意外开放管理接口。

订阅链接和节点配置都属于凭证，不上传仓库或 CI artifacts，也不要把命令的调试输出贴到公开日志。聊天中分享过的订阅 token 建议在服务商后台重置；重置是否同时撤销已有节点密码取决于服务商，需要单独确认。

后续若让发布工具通过代理访问 GitHub，应只给该工具配置代理，并使用项目级受限发布权限。先验证网络，再决定拉取式 CD 方案；出站代理可用不代表自动发布已经完成。
