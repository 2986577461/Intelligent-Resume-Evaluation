# 简历报告卡片与阅读交互

## 范围

保留自然对话，将新生成的分析回复展示为结构化报告，不改建议内容、不增加模型调用、不从 Markdown 反向识别标题或提炼摘要。

- 顶部显示目标岗位、投递类型、JD使用情况和真实生成状态；计数是已收到的条目，不预设3条/5条。
- 总体判断默认展示3行，可展开完整内容。
- 建议卡片默认显示对象、优先级、类型、“需要关注”和“建议怎么做”；长内容可展开，原文依据、岗位影响按需展开。
- 面试追问与修改建议分区；问题默认可见，考察点、准备方向和引文折叠。关联ID可以跳回并聚焦对应建议。
- “讨论这条”在输入框上方显示可取消的关联标签。发送后在用户提问上方显示可展开的关联建议卡片，气泡仅显示用户实际输入。
- 关联上下文通过独立的 `discussion_context` 参数传输；服务端将其作为未经核实的旧模型建议提供给Agent，不赋予系统指令权限，不默认重跑。原文件ID只用来限定回查对象，不作为本轮新附件。
- 正文和面试问题为17px，引用16px，辅助信息14–15px，手机端不再缩小正文；普通聊天原有20px保持不变。
- 用户向上阅读后不自动滚回底部，显示“有新内容”；文本与结构化更新按浏览器绘制帧合并，没有打字机或人为延时。
- 手机端隐藏报告旁头像，给卡片完整宽度。没有实现PDF坐标高亮，原文依据当前为引用片段。

## 展示协议 v1

保留 `[TEXT]` Markdown流作为复制、历史兼容及旧客户端的正文。新增 `[REPORT]` + JSON 对象事件：

| type | 内容 | 发布条件 |
| --- | --- | --- |
| `start` | version=1、context、file_id | 第一段真实正文即将发布 |
| `overview` | text（累计的纯文本） | 真实JSON字符串增量已解码；不声称语义已验证 |
| `action` | item | 完整、字段和引用校验通过且满足既有排序规则 |
| `question` | item | 完整、字段、引用和建议关联校验通过 |
| `complete` | 无额外字段 | 最终Schema及Markdown一致性检查通过 |
| `incomplete` | 无额外字段 | 已发布正文后中断/校验失败，不重试重放 |

服务层补充 `report_id`（消息内唯一）、`start_pos`、`end_pos`，偏移使用浏览器的UTF-16 code units，兼容emoji。范围只替换该报告对应的Markdown，保留路由模型先前或之后的普通正文。支持一条消息多个报告，ID不是全局业务任务ID。

服务端将完整或部分展示快照保存在现有 `messages.search_info.guidance_reports` 中，包含协议版本、原文件ID、上下文、已发布内容、范围和完成状态。没有数据库结构迁移；删除会话沿用现有规则。这份快照仅用于展示，不是事实缓存、原简历或续评任务状态。

- 新报告刷新后从同一快照恢复卡片。
- 用户消息的 `content` 只存实际提问，关联信息放在 `search_info.discussion_context`，展示和模型上下文分离，不新增表结构。
- `discussion_context` 限制协议版本、ID、字段及长度；原文件必须属于当前账号和会话。客户端传入的建议不视作可信事实，也不能越权读取附件。
- 上一版已经自动拼接提示词的用户消息，只对其固定格式做前端兼容拆分；不改数据库内容，不泛化裁剪普通用户文本。
- 旧报告/未知版本回退为原Markdown，不批量转换历史数据。
- 断连时客户端将尚在生成的报告标为未完成；服务端可能继续生成。刷新后以服务端保存的快照为准，不把断开连接宣称为取消后台任务。
- 最终`[DONE]`只代表传输结束，不能把缺少`complete`的报告升级为完整。
- 新后端需要配套新前端：旧前端不认识`[REPORT]`事件，已有页面应刷新。新增结构化内容使用Vue文本插值，不作为HTML执行；原有普通Markdown渲染策略不属于本轮安全改造范围。

## 验证

执行本轮验证未使用真实模型、付费API或私有简历。后端为假路由/假模型、Mock HTTP、临时SQLite；浏览器使用真实Chrome、真实Vue页面，账号、HTTP、SSE和报告都是合成假服务。

```bash
# 仓库根目录
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  python3 -m pytest ai-backend/eval -q -m 'not llm and not consistency'
npm --prefix frontend test
npm --prefix frontend run test:browser
npm --prefix frontend run build
git diff --check
```

本轮结果：后端159 passed、2 skipped、25 deselected；前端6个数据逻辑测试通过；Chrome交互冒烟通过；构建与diff检查通过。构建仍有既有包体积和依赖注释警告。构建仍有既有包体积和依赖注释警告。

浏览器脚本需要Node 22+和本机Chrome，不新增测试依赖。macOS使用默认Chrome路径，其他环境通过`CHROME_PATH`指定Chromium程序。使用隔离的临时浏览器profile并在完成后删除，不读取日常浏览器数据。可用`GUIDANCE_SCREENSHOT=/tmp/guidance-mobile.png`输出手机/桌面截图，仅包含合成材料。

覆盖：折叠展开、关联跳转、讨论上下文与取消、真实帧更新、未完成状态、向上阅读保留位置、手机无横向页面溢出、引用文本转义、刷新恢复；后端验证发布时机、引用拒绝、去重、SSE/历史正文及快照一致、emoji偏移、权限接口恢复。

未证明：真实模型是否始终遵循不重跑的意图、独立用户阅读负担是否降低、浏览器弱网/代理表现、线上旧页面升级，以及内容建议是否合理。需用真实使用反馈判断，不能把卡片化等同于语义质量提升。

## 文件与回滚

- UI：`frontend/src/components/{GuidanceReport.vue,ReportText.vue,DiscussionReference.vue,guidanceReport.js}`、`frontend/src/views/AiDialog.vue`。
- 讨论上下文：`agent/discussion.py`定义字段与模型输入边界；`agent/router.py`校验参数和附件归属，`conversation/service.py`保存提问及独立元数据。
- 协议：`agent/report_stream.py`在同一确定性渲染路径发事件，经`nodes.py`/`graph.py`/`scoring.py`/`tools.py`传递回调；`agent/service.py`传输和保存展示快照。
- 本轮前的检查点：`1e96ec6`。回滚应将本轮前后端代码作为一组处理，不只回滚前端；已有快照是可忽略的附加元数据，无需删表或迁移数据库。
