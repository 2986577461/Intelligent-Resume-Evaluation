# 有来源的简历指导 v1

## 产品边界与实际链路

目标是回答：**这条建议对应哪段原文/岗位要求、为什么提出、下一步怎么做。** 不做整体质量评分、学校名气评价、面试成功概率、模拟面试或自动投递。

1. 原有上传接口保存 PDF 文本/OCR 结果，不重新做 PDF 坐标定位。
2. 页面「简历指导」显式选择文件、确认目标岗位，JD 可留空。普通聊天入口仍保留。聊天工具 `analyze_resume` 只启动事实提取并引导页面确认，不允许聊天模型偷偷选择岗位。
3. 将非空原文行作为来源单元；来源 ID 为 `resume:<内容 SHA256 前16位>:<重复序号>`，JD 同理。相同文本跨轮 ID 稳定；引用没有坐标精度，也不证明经历真实。
4. 一个模型调用提取带原文摘录的事实，代码检查摘录确实属于对应来源。PDF 中 NBSP 等空白被模型转成普通空格时，仅按空白等价定位并恢复成原文精确切片，不进行文字模糊匹配、不改变来源 ID。SQLite 按文件和完整文本指纹保留成功提取；没有目标岗位时返回 `needs_target`，后续无需重复提取。
5. 目标确认后，用完整来源与事实做一次要求/证据/建议分析，而不是五个评分 Agent。JD 明确要求必须摘录 JD 并引用 JD ID；一般建议标为 `general`。缺少证据只能标 `insufficient`，不是用户不会。
6. 最多三条重要追问，初步指导同时返回。页面回答绑定 `parent_id + file_id + question_id`；后端在任何模型请求前检查用户、会话、文件与父结果。更换目标或 JD 必须新分析，不把旧回答转绑。续答保留上一轮要求身份。
7. 用户陈述使用独立 `user:<结果ID>:<序号>` 来源。只有用户主动勾选“确认尚未做过/掌握”，且对应要求 ID 一致，才允许 `confirmed_gap`；不从自然语言或简历遗漏自动作此判断。
8. 校验通过后保存结构化结果，再确定性生成 Markdown。报告不使用模型生成、不输出未校验 token。富展示失败时使用独立基础模板展示要求、原文、问题、理由与行动。保留普通聊天的 SSE 包装与路径；分析工具完成事件使用中性的 `guidance_returned`，不把处理失败标成分析成功。

## 局部改写的保守范围

本轮为能由代码核验“不新增原文/用户未确认的事实”，采用**有来源的整句摘编**，不开放自由事实性转述：

- 展示 `source_id` 对应原文；`sentences` 只能取该原文或肯定用户补充中的完整句子（包含原标点）。
- 可以删减、重排整句，或组合适用的用户补充，不允许跨简历项目拼入另一条原文。
- 未获原文/用户陈述支持的新技术、数字、职责，以及裁掉否定词的子串，都会被结构校验拒绝。可选改写的来源/整句约束失败时，只剔除该项，返回 `omitted_components` 并将整体标为 `partial`；不能拖垮已通过全部校验的核心建议，也不能标成完整成功。核心要求/证据/行动/追问校验失败仍拒绝整轮，不用部分有效条目掩盖错误。非法 JSON/字段结构仍需有界重试。
- 待确认内容只放 `pending_confirmation`，与建议版本分开；原始附件和提取文本永不被覆盖。
- 没有安全、有用的摘编时 `rewrites=[]`，依然给出具体的修改步骤。更流畅的自由转述需要单独的语义核查设计，不在本轮伪称已解决。

**整句来源约束依然不能证明语义没有变化。** 用户陈述可能不真实或不适用于该经历，整句重排也可能改变指代；界面要求核对语境，人工评测必须检查这些问题。

## 生产协议

唯一类型定义：`ai-backend/agent/schema.py`。提示词在调用时动态附加对应 `model_json_schema()`；评测直接导入生产类型，不再维护独立权重/JSON 报告。

| 对象 | 核心内容 |
| --- | --- |
| `Extraction` | `facts[]`：类别、原文摘录、来源 ID；不猜岗位 |
| `Guidance` | 要求/来源/证据/状态，优先行动，最多3条追问，可选摘编改写及限制 |
| `Result` | `guidance.v1`、结果/文件 ID、确认岗位、JD使用情况、来源/事实、指导、完整性、错误分类 |

状态不是质量等级：

- `success`：本轮要求均已分析，没有待追问/信息不足，也没有处理错误。确认的能力缺口可以存在，**不表示投递一定成功**。
- `partial`：有可用事实或核心指导，但有重要信息不足、追问、部分可选改写被安全校验剔除，或本轮模型处理未完成。`omitted_components` 只保存组件、原数组索引和拒绝分类，不保存不安全文案。核心分析失败时不凭空生成建议；续答失败可沿用上一轮已校验指导，并明确尚未结合新补充更新。
- `failed`：没有得到可用提取，不应解释为能力不足。
- `needs_target`：已提取事实，等待用户确认目标岗位，不根据简历推断。

`evidenced` 只表示有支持文本；`insufficient` 表示信息未提供充分；`confirmed_gap` 需要显式用户陈述。`error_code` 与这些匹配状态分开，涵盖 `text_unavailable`、`invalid_cached_extraction`、`model_unavailable`、`invalid_output`、`timeout`、`network`、`rate_limit`、`model_error`、`budget_exhausted`。

模型接入失败进一步区分 `configuration`（未配置密钥）、`authentication`（401）、`quota`（402）、`permission`（403）、`model_not_found`（404）、`request_rejected`（400/422）、`input_too_large`（413）。`processing_error` 只保留失败阶段、异常类型、HTTP 状态码和尝试次数。页面展示中文原因及排查方向；服务日志仅记录这些安全元数据，不记录异常正文、密钥或模型原文。旧记录没有这些诊断字段时仍可读取。

### API

保留既有路径，并新增同源 Cookie 鉴权接口（生产仍加 `/resume` 前缀）：

```http
POST /api/resume-guidance
Content-Type: application/json

{
  "thread_id": "当前会话ID",
  "file_id": "本会话所属文件ID",
  "target_role": "Python 后端实习生",
  "jd": "熟悉 Python\n了解 SQL"
}
```

返回 `{result, markdown, user_message}`，其中 `result` 为生产结构化协议。缺岗位也允许提交，先提取后要求确认。业务处理失败仍返回有明确状态的 `Result`；参数非法为 422，越权为 404，旧结果与当前提取文本不一致为 409，忙碌为 429，不将这些错误伪装为成功分析。

续答示例（目标和 JD 保留原值）：

```json
{
  "thread_id": "当前会话ID",
  "file_id": "同一文件ID",
  "target_role": "Python 后端实习生",
  "jd": "熟悉 Python\n了解 SQL",
  "parent_id": "之前返回的结果ID",
  "answers": [
    {"question_id": "之前追问ID", "text": "我尚未做过 SQL 索引优化。", "confirms_absence": true}
  ]
}
```

`GET /api/resume-guidance/{file_id}/{result_id}?thread_id=...` 读取所属文件的结构化结果。聊天消息 `search_info.guidance` 同时保留结果，前端刷新历史后可以恢复追问表单，避免靠模型猜测上一轮分析对象。

## 存储与迁移

在既有 `APP_DATA_DIR/app.db` 增量建表，无新数据库服务：

- `guidance_extractions`：文件外键、`guidance.v1:<全文 SHA256>`、已校验提取 JSON；不同岗位/多次续答复用。
- `guidance_results`：不可变结果 ID、文件外键、结构化 JSON；失败/不完整也保留真实状态。
- 文件删除会级联清除上述数据，所有读取/续答都先验证原文件所有权。

旧评分图、七份评分提示词、权重/分段/实体命中评测与旧评分样本已删除，改用完全合成案例。旧聊天历史不重写，可能保留旧评分内容；它们不是新协议结果。旧 Redis pending 缓存不读取，任其原 TTL 过期；首次新分析会重新做一次事实提取。Redis 服务和健康检查仍保留，避免改变现有部署。

外层聊天 checkpoint **不是指导工作流断点**。当前仅持久化成功提取和最终状态结果；进程在指导调用中退出后，重新提交可复用提取，但不会自动恢复在途调用或声称已完成。

## 可靠性与配置

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `DEEPSEEK_BASE_URL` | 官方地址 | 指导与 OCR 的 API 地址；指导优先读取此项，其次兼容 `DEEPSEEK_API_BASE` |
| `MODEL_NAME` | deepseek-v4-flash | 指导模型名；必须由当前账户/网关支持 |
| `GUIDANCE_REQUEST_TIMEOUT_SECONDS` | 25 | 模型单次传输请求超时 |
| `GUIDANCE_BUDGET_SECONDS` | 90 | 一轮模型处理的总时间预算 |
| `GUIDANCE_MAX_ATTEMPTS` | 2 | 每阶段最大尝试次数（含首次，夹在1到3之间） |

- 指导模型使用 JSON 对象模式，并显式关闭 DeepSeek `thinking`，让有限时延用于结构化回答而非隐藏推理。保留原单次 25 秒与总计 90 秒默认预算；不为掩盖问题无限增加超时。
- 指导模型设置 `max_retries=0`；业务只对瞬时网络、429/服务端错误、超时和非法结构化输出有限重试，短退避也计入预算。按异常继承关系识别 `OpenAITimeoutError`、`OpenAIConnectionError` 等新 SDK 包装，避免误判为非重试的 `model_error`。权限等非瞬时错误直接失败。
- 每次调用传入 `min(单次超时, 剩余预算)`，每次响应返回后再次检查预算；晚到响应不算成功。提取成功后即持久化，不因分析重试重复提取。
- 没有 `Future.result(timeout)` 式的伪取消。底层同步请求实际返回之前，不释放单实例的两槽并发许可。客户端 120 秒等待超时仅表示连接/等待失败，不表示服务端或模型已取消，页面明确提示勿立即重复提交。
- HTTP SDK/传输层超时未必是严格墙钟截止（例如缓慢持续响应、DNS/SDK/系统调度）。本轮是显式传输超时＋逻辑总预算，**不是可强制杀死底层请求的任务平台**。
- 分析过程中只发状态，不发待校验模型内容；完整校验后一次性输出，所以重试不会拼接部分报告。没有第三次“报告润色”调用。

### 未覆盖的风险

- 无在途任务持久化、崩溃自动恢复、跨进程协调或请求幂等键；多 worker 部署时并发限制按进程计算。重复提交可能额外计费。
- 指导结果入库与聊天消息不是同一事务；连接断开或消息保存失败时，文件级结果可能已经存在，但聊天历史不一定完整。不要把请求失败解释为模型未调用。
- 现有上传大小/页数限制、OCR 和普通聊天（包括标题生成）的超时/配额、普通聊天线程生命周期与日志隐私不在本轮统一治理。没有为这些旧路径宣称同样的指导预算保障。
- 文本解析可能漏行或顺序不佳，提取也可能漏事实；来源存在/摘录匹配/状态校验不能证明事实真实、建议相关或 JD 主要要求覆盖充分。恶意原文提示注入仍需真实模型核查。
- 没有浏览器端自动化测试，前端只做构建检查与后端真实路由离线回归；上线前需人工检查页面提交、刷新历史、追问和会话切换。

## 本地验证

按 README 启动既有前后端；上传一个合成 PDF，展开「简历指导」，选择文件、输入目标岗位、可选粘贴 JD，再确认分析。补充只在具体结果下填写，勾选否定确认前核对对应要求。

标准离线命令（需 `requirements-ci.txt` 中的 `pytest-socket`）：

```bash
cd ai-backend
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  python -m pytest -v -m "not llm and not consistency" --disable-socket --allow-unix-socket
```

覆盖岗位/JD 数据流、缺岗位复用、未写不等于不会、JSON/字段/引用、改写新事实、预算与分类重试、展示回退、文件和问题归属、缓存损坏、删除级联、生产协议一致性，以及原有账号/上传/隔离回归。mock 只证明受控输入输出下代码的约束，不证明真实模型不幻觉。

默认真实模型案例用 `eval/cases/guidance.json` 中三份合成文本。默认排除；即使用 `-m llm`，也必须显式设置 `RUN_PAID_GUIDANCE_EVAL=1`。不会加载 `.env`，密钥由操作者另行设置；会产生费用。评测产物写 pytest 临时目录，建议固定到 `/tmp` 后人工查看。

```bash
RUN_PAID_GUIDANCE_EVAL=1 python -m pytest -m llm -v --basetemp=/tmp/resume-guidance-eval
```

### 指定 PDF 的 SDK 集成测试与真实复测

`eval/test_guidance_sdk.py` 经由真实上传/指导路由、临时数据库及真实 LangChain → DeepSeek → OpenAI SDK 执行。默认使用生成的合成 PDF，HTTP 由 `httpx.MockTransport` 接管，不产生费用。它验证配置地址、JSON/非思考模式、请求超时、SDK 包装异常重试、错误分类及 SDK 无隐藏重试，不是模型质量测试。仅安装最小 CI 依赖而没有完整模型 SDK 时，相应集成测试显式跳过，独立离线测试仍覆盖异常子类与空白引用恢复。

在 `ai-backend` 下指定用户授权的文件做离线集成测试：

```bash
GUIDANCE_TEST_PDF='../test/免费在线简历制作工具-CodeCV简历-2.pdf' \
  python -m pytest eval/test_guidance_sdk.py -m 'not llm' -v \
  --disable-socket --allow-unix-socket --basetemp=/tmp/guidance-pdf-offline
```

真实复测需由运行环境提供 `DEEPSEEK_API_KEY` 和对应地址；测试不会自动加载 `.env`。下面的显式开关允许将此 PDF 提取文本发送给配置的模型服务，并可能产生费用。测试岗位是独立传入的参数，不让模型推断。结果和报告仅写到临时测试目录，可能包含个人信息，勿提交到仓库。

```bash
GUIDANCE_PDF_LIVE=1 \
GUIDANCE_TEST_PDF='../test/免费在线简历制作工具-CodeCV简历-2.pdf' \
GUIDANCE_TEST_ROLE='AI智能应用开发实习生' \
  python -m pytest eval/test_guidance_sdk.py::test_live_pdf_guidance -m llm -v -s \
  --basetemp=/tmp/guidance-pdf-live
```

人工核查每个案例的 `review`，并逐条确认：

1. 主要要求是否覆盖合理，JD 原文和模型一般建议是否明确分开。
2. 关键观察是否真的被所引原文支持，而不仅是引用 ID 存在；对遗漏信息有没有不当断言不会。
3. 行动能否执行，缺数据时有没有说明验证场景/步骤或诚实描述方式，而不是生成数字。
4. 追问是否少量且会改变建议；已回答的问题是否避免重复追问。
5. 改写是否保持否定、职责范围、时间与项目归属，用户补充是否确实适用，不把注入指令当事实。
6. 不因学校名气、页数或模型自信给出能力等级、质量总分和录用预测。

任何一项语义失败都应记录案例和具体引用，不能用“Schema 通过”掩盖。
