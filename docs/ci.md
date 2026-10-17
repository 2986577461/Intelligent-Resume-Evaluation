# 基础 CI

配置位于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。每次 push、PR 更新时自动运行，也可以在 GitHub Actions 页面手动触发；不部署、不需要配置任何 Secrets。

## 检查内容

三个任务独立运行：

| GitHub 检查名称 | 环境 | 执行内容 |
| --- | --- | --- |
| `Frontend build` | Node.js 24 / Ubuntu | 根据 `frontend/package-lock.json` 执行 `npm ci`，再执行 `npm run build` |
| `Backend offline tests` | Python 3.12 / Ubuntu | 安装 `ai-backend/requirements-ci.txt`，检查依赖兼容性，再运行不带 `llm`、`consistency` 标记的 pytest 测试 |
| `Deployment helper tests (offline)` | Ubuntu / Python 标准库 | 模拟 SSH，测试凭证清理、发布包安全解压、页面原子切换和失败回退，不访问服务器 |

后端通过 `pytest-socket --disable-socket --allow-unix-socket` 禁止 TCP/IP 网络连接（仅允许测试客户端事件循环使用本地 Unix socket）。离线测试收集阶段不读取 `.env`、不初始化真实模型，并关闭 LangSmith tracing；不需要模型密钥、数据库服务或线上环境。

Python CI 依赖单独锁定，包含离线评测及账号/文件 API 测试所需的直接和传递依赖，不安装运行完整服务所需的向量库、嵌入模型或真实模型 SDK。**这项检查不能替代完整服务启动验证，也不代表生产环境依赖已经锁定。** 前端目前检查构建，不包含单元测试或浏览器端测试。

部署辅助脚本的离线测试会模拟 SSH，检查参数校验、固定主机公钥要求和临时凭证清理，不使用 Secrets，也不连接服务器。生产发布另见 [docs/cd.md](cd.md)。

## 本地复现

前端使用 Node.js 24，在仓库根目录执行：

```bash
cd frontend
npm ci --no-audit --no-fund
npm run build
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

同时提交 `.in` 和 `.txt`。运行时依赖仍维护在 `requirements.txt`，这份 CI 锁文件不用于生产部署。

## 合并保护

工作流首次推送并运行后，在 GitHub 仓库 Settings → Rules → Rulesets（或 Branches → Branch protection）中，为目标分支配置：

- 要求通过 Pull Request 合并。
- 要求 `Frontend build` 和 `Backend offline tests` 两个检查通过。

提交工作流文件本身不会自动开启分支保护，需要仓库管理员在 GitHub 上设置。
