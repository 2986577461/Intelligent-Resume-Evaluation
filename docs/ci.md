# 基础 CI

配置位于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。每次 push、PR 更新时自动运行，也可以在 GitHub Actions 页面手动触发；不部署、不需要配置任何 Secrets。

## 检查内容

三个任务独立运行：

| GitHub 检查名称 | 环境 | 执行内容 |
| --- | --- | --- |
| `Frontend build` | Node.js 24 / Ubuntu | 根据 `frontend/package-lock.json` 执行 `npm ci`，设置生产 `VITE_PUBLIC_ORIGIN`，再执行 `npm run build -- --base=/resume/` |
| `Backend offline tests` | Python 3.12 / Ubuntu | 安装 `ai-backend/requirements-ci.txt`，检查依赖兼容性，再运行不带 `llm`、`consistency` 标记的 pytest 测试 |
| `Deployment helper tests (offline)` | Ubuntu / Python 标准库 | 模拟 SSH，测试凭证清理、发布包安全解压、页面原子切换和失败回退，不访问服务器 |

后端通过 `pytest-socket --disable-socket --allow-unix-socket` 禁止 TCP/IP 网络连接（仅允许测试客户端事件循环使用本地 Unix socket）。离线测试收集阶段不读取 `.env`、不初始化真实模型，并关闭 LangSmith tracing；不需要模型密钥、数据库服务或线上环境。

Python CI 依赖单独锁定，包含离线评测及账号/文件 API 测试所需的直接和传递依赖，不安装运行完整服务所需的向量库、嵌入模型或真实模型 SDK。**这项检查不能替代完整服务启动验证。** 生产镜像另用 `requirements-runtime.txt` 锁定依赖，由 Docker 构建执行 `pip check`，由发布冒烟验证完整运行环境。 前端目前检查构建，不包含单元测试或浏览器端测试。

部署辅助脚本的离线测试会模拟 SSH，检查参数校验、固定主机公钥要求和临时凭证清理，不使用 Secrets，也不连接服务器。生产发布另见 [docs/cd.md](cd.md)。

## 本地复现

前端使用 Node.js 24，在仓库根目录执行：

```bash
cd frontend
npm ci --no-audit --no-fund
VITE_PUBLIC_ORIGIN=https://www.myzyitzj.cn:19443 npm run build -- --base=/resume/
```

后端建议使用 Python 3.12 的独立虚拟环境，避免现有全局依赖掩盖漏装的问题。在仓库根目录执行：

```bash
python3.12 -m venv /tmp/resume-ci-venv
source /tmp/resume-ci-venv/bin/activate
cd ai-backend
python -m pip install -r requirements-ci.txt
python -m pip check
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  python -m pytest -v -m "not llm and not consistency" --disable-socket --allow-unix-socket
```

新增普通离线测试会自动纳入 CI。需要真实模型的评测必须标记 `llm` 或 `consistency`，不会在此 CI 中执行；手动执行这些评测前，需要另行安装完整应用依赖并配置模型密钥。

## 更新依赖

前端修改依赖后同时提交 `package.json` 和 `package-lock.json`；CI 使用 `npm ci`，不会自动修正不一致的锁文件。

后端离线测试需要新依赖时，修改 `ai-backend/requirements-ci.in`，再在 Python 3.12 虚拟环境中生成锁文件：

```bash
cd ai-backend  # 从仓库根目录进入；若已在该目录则省略
python -m pip install pip-tools==7.6.2
pip-compile --strip-extras --output-file=requirements-ci.txt requirements-ci.in
# 有意升级所有锁定依赖时，在上一条命令加 --upgrade。
python -m pip install -r requirements-ci.txt
python -m pip check
python -m pytest -v -m "not llm and not consistency" --disable-socket --allow-unix-socket
```

同时提交 `.in` 和 `.txt`。生产镜像使用 `requirements-runtime.in` / `requirements-runtime.txt`；`requirements.txt` 用于完整开发环境。这份 CI 锁文件不用于生产部署。

## 合并保护

工作流首次推送并运行后，在 GitHub 仓库 Settings → Rules → Rulesets（或 Branches → Branch protection）中，为生产分支 **`main`** 配置：

- 要求通过 Pull Request 合并。
- 要求 `Frontend build`、`Backend offline tests` 和 `Deployment helper tests (offline)` 三个检查通过。

提交工作流文件本身不会自动开启分支保护，需要仓库管理员在 GitHub 上设置，并限制绕过规则的直接 push。默认分支为 `main` 不影响本地继续在 `dev` 开发；通过 `dev` → `main` 的 PR 合并准备发布的代码。修改默认分支不会同步两条分支的内容；合并只触发 CI，生产仍须从 `main` 手动发布。

## 发布时的业务冒烟

普通 CI 不运行 Docker 集成测试。手动发布在打包之前运行 `scripts/cd/smoke-release.sh`：

- 创建临时自签名 TLS 证书，客户端显式信任该证书，**不跳过 TLS 校验**。
- 使用生产 Nginx 路由（仅把容器监听端口改为 19443）和正式前端产物。
- Redis、后端、Nginx 位于 `--internal` Docker 网络，不发布宿主机端口，不能访问外网模型 API。
- 验证 HTTPS 页面与全部构建静态资源（含懒加载模块）、版本号、注册、Secure/HttpOnly/SameSite Cookie、会话恢复、退出撤销、重新登录、文字 PDF 上传/下载/删除。
- 账号和文件仅写入临时容器；成功失败都会清理容器、网络和临时证书。不调用 SSE/模型/OCR，也不修改生产数据。
- 失败仅输出容器状态、退出码、OOM 和健康状态，避免原始日志泄漏凭证或简历内容。

离线后端测试还会把同一冒烟 Python 脚本接到真实业务路由，检查脚本本身的请求、Cookie 和文件操作逻辑；这不替代 Docker/Nginx/TLS 集成测试。
这些检查是 HTTP/API 冒烟，不是浏览器 UI 自动化，也不证明真实模型输出质量。

本地完整复现需要正在运行的 Docker、OpenSSL 和已构建的生产前端，在仓库根目录执行：

```bash
SHA=$(git rev-parse HEAD)
docker build -t "resume-backend:$SHA" -f deploy/resume/Dockerfile .
SHA="$SHA" bash scripts/cd/smoke-release.sh
```

脚本使用固定 `smoke-*` 容器名，仅在独立测试 Docker 环境运行，不要并发执行，不要在生产宿主机运行。
