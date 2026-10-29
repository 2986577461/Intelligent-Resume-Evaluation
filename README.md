# 简历指导

[![CI](https://github.com/2986577461/Intelligent-Resume-Evaluation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/2986577461/Intelligent-Resume-Evaluation/actions/workflows/ci.yml)

基于 Vue 3、FastAPI 和 LangGraph 的对话式简历指导项目。主要验收场景为已有初稿的技术实习、校招求职者：提供有依据的优先修改建议，以及相关面试追问和准备方向。上传 PDF 或图片后先提取事实，再结合消息识别岗位和投递类型；缺项时暂存并追问，补充后继续。外层 Agent 保留工具选择，内部使用一次综合分析，不做数字评分。

账号使用本地邮箱＋密码注册、登录，不依赖外部用户服务。模型、视觉识别和联网搜索仍需要相应的 API 服务，调用费用由部署者承担。

## 功能

- **简历识别**：PDF 按页面位置重建阅读顺序，减少行内代码和标签被提到末尾的问题；归一化特殊字形，识别明确岗位标题，避免重复询问。位置排序不是通用多栏布局识别。扫描版 PDF 和图片通过 DeepSeek 视觉模型 OCR。
- **投递前修改（A）**：一次综合检查全文，输出最多3条重点建议，展示对象、原文依据、观察与问题、岗位影响及下一步，区分表达调整、补充证据、能力实践和信息确认；没有重点问题时允许空建议，不凑固定模块。
- **面试前准备（B）**：最多5个针对实际内容的追问，展示依据、考察点与准备方向，可关联某条建议；不将参考方案编成用户经历。报告后可普通追问，不默认重跑分析。
- **先解析再续评**：从消息和简历识别岗位及实习/校招/社招，信息充分就分析，缺什么问什么。默认按岗位名称分析，JD 是可选补充，提供时进一步对照具体职责和任职要求，不提供也正常分析。事实与投递上下文分开暂存，Redis 故障也不丢失已确认信息。
- **提取复用**：成功提取按用户/会话/文件、文本指纹、协议/提示词/模型版本缓存；当前文件的提取和投递上下文还会本地持久化，用于追问后恢复。
- **流式对话**：真实接收综合模型 JSON 流，总体判断增量展示；高优先级建议完整且引用校验通过即展示，其他建议与追问按校验和排序约束发布。不等待整份报告，也没有人为状态播放或打字延迟。中途失败明确标注报告未完成。
- **分层报告阅读**：新报告以建议卡片和面试准备分区展示，依据与详细影响按需展开；支持单条建议讨论、流式阅读位置保留和刷新恢复，旧历史兼容 Markdown。实现与验收见 [报告界面说明](docs/guidance-ui.md)。
- **会话管理**：保存历史消息和附件，按登录账号隔离访问。
- **本地账号**：邮箱注册、登录、恢复登录状态和退出登录。

前端目前支持选择 PDF、PNG、JPG、JPEG、WebP；后端上传接口另外接受 GIF。

## 技术栈与目录

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Pinia、Element Plus、Axios |
| API | Python 3.12、FastAPI、Uvicorn |
| AI 工作流 | LangChain、LangGraph、DeepSeek、Tavily |
| 数据 | SQLite（账号、登录会话、聊天记录、附件、Agent 检查点）、Redis（成功提取缓存） |
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
│   ├── agent/                # 指导图、投递续评状态、提示词、工具和 SSE
│   ├── conversation/         # 会话、消息、附件持久化
│   ├── files/                # 文件上传、提取、预览与清理
│   ├── common/               # 视觉模型客户端、日志、同源请求保护
│   ├── state/                # Redis 缓存
│   ├── eval/                 # 离线测试、账号/API 测试、真实模型评测
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
| `DEEPSEEK_API_KEY` | 对话、指导和视觉识别所需的 DeepSeek 密钥 |
| `DEEPSEEK_BASE_URL` | 指导与视觉识别的 API 地址，默认 `https://api.deepseek.com`；指导兼容旧 `DEEPSEEK_API_BASE` |
| `DEEPSEEK_VISION_MODEL` | OCR 模型，默认 `deepseek-v4-flash-vision-exp`；需确认账号有对应模型权限 |
| `TAVILY_API_KEY` | 联网搜索密钥；当前应用启动时会初始化搜索工具 |
| `REDIS_URL` | 默认 `redis://localhost:6379/0`，成功提取缓存默认保留 1 小时；故障不阻止指导或本地续评 |
| `SESSION_COOKIE_SECURE` | 本地 HTTP 使用 `false`；生产 HTTPS 必须设为 `true` |
| `MODEL_NAME`（可选） | 指导工具和真实模型评测的模型名，默认 `deepseek-v4-flash`；主对话模型目前仍在 `main.py` 中指定 |
| `APP_DATA_DIR`（可选） | 账号、登录会话、消息和附件数据库目录，默认 `ai-backend/conversation/db/`；不改变 Agent 检查点目录 |
| `SCORING_REQUEST_TIMEOUT_SECONDS`（可选） | 指导节点单次传输超时，保留旧变量名，默认 60 秒；SDK 不自动重试，图节点最多尝试 2 次 |

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
| `ai-backend/conversation/db/app.db` | 账号、登录会话、限流计数、聊天消息、原始附件、提取文本及当前简历续评上下文 |
| `ai-backend/agent/db/conversations.db` | Agent 对话检查点 |
| `ai-backend/common/logs/` | Agent 日志 |

SQLite 使用 WAL，备份应使用 SQLite 备份工具，或停服后连同相关数据库文件处理，避免只复制运行中的单个 `.db`。简历会发送给配置的模型服务，日志也可能包含处理内容，部署时应明确告知使用者并保护数据。

## 测试与 CI

离线测试不需要模型密钥或 Redis，包括指导图、按缺项追问、持久化恢复、引用回查，以及注册、登录、会话过期、退出、文件上传和跨账号访问隔离。建议在独立 Python 3.12 环境中执行：

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

真实模型评测需要安装完整应用依赖并配置密钥，**会产生 API 费用**，不在基础 CI 中执行：

```bash
# 在 ai-backend 目录、完整应用环境中执行
python -m pytest -m llm -v
python -m pytest -m consistency -v
```

评测直接使用生产 `report_data` 指导协议；用户端 Markdown 由同一份结构化结果渲染。离线测试不能证明真实模型的语义质量，引用存在也不代表判断正确。需求和预先标注样本见 [A/B 需求与验收](docs/guidance-acceptance.md)，已执行的真实模型结果及失败案例见 [真实评测记录](docs/guidance-evaluation-results.md)，流程、状态留存和成本边界见 [简历指导工作流](docs/scoring-workflow.md)。目前未证明单次分析与旧多节点方案质量等同，也未证明优于直接使用通用 AI。

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
