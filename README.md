# 智能简历指导

[![CI](https://github.com/2986577461/Intelligent-Resume-Evaluation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/2986577461/Intelligent-Resume-Evaluation/actions/workflows/ci.yml)

基于 Vue 3、FastAPI 和 LangGraph 聊天入口的简历指导项目。围绕用户确认的目标岗位，依据简历原文和可选 JD，识别匹配证据、表达问题与信息缺口，返回有依据的行动建议、少量追问和保守局部改写。不提供整体质量分、学校名气评价或录用概率。

最小闭环：上传简历 → 在「简历指导」选择文件并确认目标岗位 → 分析事实与要求 → 必要时按文件补充 → 查看来源、行动建议与原文/建议版本。没有 JD 也能使用，会明确说明依据是岗位一般要求。

账号使用本地邮箱＋密码注册、登录，不依赖外部用户服务。模型、视觉识别和联网搜索仍需要相应的 API 服务，调用费用由部署者承担。

## 功能

- **简历识别**：PDF 文本提取；扫描版 PDF 和图片通过 DeepSeek 视觉模型 OCR。
- **证据指导**：原文片段有稳定 ID；提取事实和要求对齐结果通过生产 Schema、来源引用及状态校验。
- **有限补充**：缺少岗位时要求用户确认；已提取事实按文件/文本指纹存入 SQLite，续答不重新提取。用户陈述与原简历来源分开。
- **有界改写**：展示原文、整句摘编版本、理由和待确认内容，不生成未经确认的技术、职责或数字，不覆盖原文件。
- **普通聊天**：保留 SSE 聊天和搜索。指导使用显式 POST，先校验再确定性生成 Markdown，不直接流出模型报告 token。
- **会话管理**：保存历史消息和附件，按登录账号隔离访问。
- **本地账号**：邮箱注册、登录、恢复登录状态和退出登录。

前端目前支持选择 PDF、PNG、JPG、JPEG、WebP；后端上传接口另外接受 GIF。

## 技术栈与目录

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Pinia、Element Plus、Axios |
| API | Python 3.12、FastAPI、Uvicorn |
| AI 工作流 | LangChain、LangGraph、DeepSeek、Tavily |
| 数据 | SQLite（账号、登录会话、聊天记录、附件、文件级事实与指导结果、聊天检查点）；Redis 保留现有部署探活 |
| 测试与 CI | pytest、假模型离线测试、FastAPI TestClient、GitHub Actions |

```text
.
├── frontend/
│   ├── src/components/       # 登录/注册弹窗、导航栏
│   ├── src/views/            # 简历上传与对话页面
│   ├── src/request/          # API 请求与 Cookie 登录会话
│   └── vite.config.js        # 开发代理
├── ai-backend/
│   ├── auth/                 # 本地账号、密码散列、服务端登录会话
│   ├── agent/                # 指导链路、生产 Schema、提示词、聊天工具和 SSE
│   ├── conversation/         # 会话、消息、附件持久化
│   ├── files/                # 文件上传、提取、预览与清理
│   ├── common/               # 视觉模型客户端、日志、同源请求保护
│   ├── state/                # 文件级提取及指导结果 SQLite 存储
│   ├── eval/                 # 离线数据流/账号/API 测试、合成案例及人工核查标准
│   ├── main.py               # FastAPI 入口
│   ├── .env.example          # 环境配置示例
│   ├── requirements.txt      # 完整应用依赖
│   └── requirements-ci.txt   # 离线测试依赖锁文件
├── .github/workflows/ci.yml
└── docs/ci.md                # CI 说明与依赖更新方法
```

## 本地运行

### 1. 准备环境

需要 Python 3.12、Node.js 24、npm，以及可连接的 Redis。推荐用虚拟环境安装 Python 依赖；下面把环境放在仓库外，避免误提交。

```bash
git clone https://github.com/2986577461/Intelligent-Resume-Evaluation.git
cd Intelligent-Resume-Evaluation
python3.12 -m venv "$HOME/.venvs/resume-evaluation"
source "$HOME/.venvs/resume-evaluation/bin/activate"
python -m pip install -r ai-backend/requirements.txt
cp ai-backend/.env.example ai-backend/.env
```

编辑 `ai-backend/.env`，填写真实 API 密钥。不要提交 `.env`。

| 配置项 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 对话、简历指导和视觉识别所需的 DeepSeek 密钥 |
| `DEEPSEEK_BASE_URL` | 视觉识别及简历指导使用的 API 地址；指导优先读取此项，其次兼容 `DEEPSEEK_API_BASE`，未设置时使用官方地址 |
| `DEEPSEEK_VISION_MODEL` | OCR 模型，默认 `deepseek-v4-flash-vision-exp`；需确认账号有对应模型权限 |
| `TAVILY_API_KEY` | 联网搜索密钥；当前应用启动时会初始化搜索工具 |
| `REDIS_URL` | 现有部署与 `/api/health` 仍需要 Redis；新指导状态不依赖 Redis |
| `SESSION_COOKIE_SECURE` | 本地 HTTP 使用 `false`；生产 HTTPS 必须设为 `true` |
| `MODEL_NAME`（可选） | 指导模型名，默认 `deepseek-v4-flash`；普通聊天模型仍在 `main.py` 中指定 |
| `GUIDANCE_REQUEST_TIMEOUT_SECONDS` | 指导单次模型请求超时，默认 25 秒；SDK 重试关闭 |
| `GUIDANCE_BUDGET_SECONDS` | 指导一轮模型处理总预算，默认 90 秒；每次请求取剩余预算与单次超时较小值 |
| `GUIDANCE_MAX_ATTEMPTS` | 每阶段最大尝试次数（含首次），默认 2，上限 3；仅重试瞬时错误、限流或非法结构输出 |
| `APP_DATA_DIR`（可选） | 账号、登录会话、消息和附件数据库目录，默认 `ai-backend/conversation/db/`；不改变 Agent 检查点目录 |

模型名称是否可用取决于服务商和账号权限，配置密钥不代表一定有访问权限。

### 2. 启动 Redis

如果本地已有 Redis，确认它与 `REDIS_URL` 一致即可。也可以用 Docker 启动一个仅绑定本机的开发实例：

```bash
docker run -d --rm --name resume-redis \
  -p 127.0.0.1:6379:6379 redis:7-alpine
```

### 3. 启动后端

在仓库根目录、已激活 Python 环境的终端执行：

```bash
cd ai-backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

接口文档：<http://127.0.0.1:8000/docs>。当前后端不托管 Vue 构建产物，页面通过下面的前端服务访问。

### 4. 启动前端

另开终端，从仓库根目录执行：

```bash
cd frontend
npm ci
npm run dev
```

打开 Vite 输出的地址（通常为 <http://localhost:5173>），点击「登录 / 注册」，使用邮箱和至少 12 位密码注册。注册成功后自动登录，再上传简历、发送评估请求。

开发环境由 Vite 把 `/api` 和 `/chat-stream` 代理到 Python，不再需要 Spring Boot 或外部登录接口。

## 账号接口

以下是 Python 后端的原始路径。前端生产环境通过 `/resume` 前缀访问，注册和登录成功后由浏览器接收 Cookie，不需要手动保存 Token。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | JSON 请求体为 `email`、`password`；创建账号并自动登录 |
| POST | `/api/auth/login` | 使用邮箱和密码登录 |
| GET | `/api/auth/me` | 获取当前账号的 `id`、`email`、`name` |
| POST | `/api/auth/logout` | 撤销当前登录会话并清除 Cookie |

完整接口见运行后端后的 `/docs`。会话、上传、文件预览和 SSE 都需要有效登录 Cookie。

## 账号与数据

- 邮箱作为登录标识，不区分大小写；密码长度为 12–128 个字符，使用带随机盐的 scrypt 散列，不保存明文密码。
- 登录状态通过 `HttpOnly`、`SameSite=Lax` Cookie 保存，默认有效期 7 天。服务端仅保存随机会话凭证的 SHA-256 摘要，退出时撤销会话。
- 前端不再保存登录 Token，也不会把凭证拼到 SSE URL。注册和登录有基于 SQLite 的请求频率限制。
- **目前没有验证邮件、邮箱所有权验证或密码找回功能**，不应把注册邮箱视为已验证身份。
- 旧外部账号数据不会自动归属到新邮箱账号。升级前请备份数据库；如需迁移，应另行制定明确的账号映射，不能仅凭邮箱或昵称认领旧数据。

默认持久化目录：

| 路径 | 内容 |
| --- | --- |
| `ai-backend/conversation/db/app.db` | 账号、登录会话、限流计数、聊天消息、原始附件与提取文本 |
| `ai-backend/agent/db/conversations.db` | Agent 对话检查点 |
| `ai-backend/common/logs/` | Agent 日志 |

SQLite 使用 WAL，备份应使用 SQLite 备份工具，或停服后连同相关数据库文件处理，避免只复制运行中的单个 `.db`。简历会发送给配置的模型服务，日志也可能包含处理内容，部署时应明确告知使用者并保护数据。

## 测试与 CI

离线测试不需要模型密钥或 Redis，包括指导数据流、JSON/字段/引用校验、补充绑定、超时重试、展示回退，以及注册、登录、会话过期、退出、文件上传和跨账号访问隔离。建议在独立 Python 3.12 环境中执行：

```bash
python3.12 -m venv "$HOME/.venvs/resume-evaluation-ci"
source "$HOME/.venvs/resume-evaluation-ci/bin/activate"
cd ai-backend  # 从仓库根目录执行
python -m pip install -r requirements-ci.txt
python -m pip check
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  python -m pytest -v -m "not llm and not consistency" --disable-socket --allow-unix-socket
```

只允许测试客户端内部使用 Unix socket，禁止 TCP/IP 联网。测试使用临时 SQLite 数据库，不修改正式账号或会话数据。

生产 Schema 位于 `agent/schema.py`，评测直接复用该协议；Markdown 只由校验后的结果生成。旧分数区间/权重/实体命中评测已移除，不再维护另一份 JSON 报告协议。

可选真实模型评测必须安装完整依赖、在终端配置密钥，并显式同意费用；默认不运行、不读取 `.env`：

```bash
RUN_PAID_GUIDANCE_EVAL=1 python -m pytest -m llm -v --basetemp=/tmp/resume-guidance-eval
```

这只检验协议和来源约束，不能证明语义质量。合成案例、人工核查标准、状态/超时边界及迁移说明见 [指导链路文档](docs/resume-guidance.md)。

GitHub Actions 在 push、PR 更新时执行前端构建、后端离线测试和部署脚本离线测试，不自动部署。完整说明见 [docs/ci.md](docs/ci.md)。

## 部署注意事项

前端通过 `npm run build` 生成 `frontend/dist/`，由静态服务器托管；Python 服务单独运行。生产环境前端会为 API 加 `/resume` 前缀，需要同源反向代理将 `/resume/api/...`、`/resume/chat-stream` 去掉 `/resume` 后转发到后端。

- 使用 HTTPS，并设置 `SESSION_COOKIE_SECURE=true`；代理保留原始 `Host`，以通过同源请求检查。当前不开放跨域 Cookie 请求。
- SSE 代理必须关闭缓冲，并配置足够的读取超时；不要把 `/chat-stream` 当作普通静态接口缓存。
- 持久化并备份 SQLite 目录，确保应用进程有写权限。
- 生产不要使用 `--reload`，不要把 Redis、数据库文件、日志或 `.env` 暴露到公网。
- 当前只有账号接口的基础限流；公开运营前仍需增加评估配额、上传大小限制和网关限流，控制模型费用及资源滥用。

分支流程为 `dev` 日常开发 → PR/CI 合并到 `main` → 从 `main` 手动发布；合并不自动上线。CD 使用 GitHub Actions 手动发布版本包，服务器通过本机代理主动拉取、校验、备份数据库并部署；失败恢复旧容器和页面入口。生产入口为 `https://www.myzyitzj.cn:19443/resume/`，配置和运维见 [docs/cd.md](docs/cd.md)。普通 push/PR 不自动部署。

发布前增加隔离 HTTPS/账号/文件业务冒烟；部署后执行只读页面与资源校验，并记录结构化部署状态，支持可选、去重的 webhook 通知。服务器侧脚本与 systemd 更新需要单独审核安装，详见 [CD 运维文档](docs/cd.md#部署状态和通知)。
