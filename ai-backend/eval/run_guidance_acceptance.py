"""显式付费评测：只发送验收文档 S1–S8 合成片段，不读取私有简历。

cd ai-backend && python -m eval.run_guidance_acceptance --output /tmp/guidance-eval.json
输出含合成模型结果和逐次指标；不包含密钥，不默认运行。
"""
import argparse
import hashlib
import json
import os
import re
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def load_cases():
    text = (Path(__file__).resolve().parents[2] / "docs/guidance-acceptance.md").read_text()
    cases = []
    for match in re.finditer(r"### (S\d+) ([^\n]+)\n(.*?)(?=\n### |\n## )", text, re.S):
        case_id, title, block = match.groups()
        quotes = re.findall(r"^> (.+)$", block, re.M)
        cases.append({"id": case_id, "title": title, "resume_text": quotes[0],
                      "user_message": quotes[1] if len(quotes) > 1 else "请按后端开发校招分析这份简历",
                      "criteria": block.split("验收：", 1)[-1].strip()})
    if len(cases) != 8:
        raise ValueError("应有8个明确的合成验收案例")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--routes", action="store_true", help="额外执行7个真实路由选择案例，工具不执行")
    parser.add_argument("--case", action="append", dest="case_ids", help="只复测指定合成案例，如 --case S7")
    args = parser.parse_args()
    cases = load_cases()
    if args.case_ids and set(args.case_ids) - {case["id"] for case in cases}:
        parser.error("--case 必须是 S1–S8")
    # 禁止评测数据被 tracing 上传到第二个服务。
    os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"
    from agent.graph import build_eval_graph
    from agent.scoring import create_scoring_model
    from agent.telemetry import EvaluationTrace

    class RecordingModel:
        def __init__(self, delegate):
            self.delegate = delegate
            self.raw_stream = ""
        def invoke(self, *args, **kwargs):
            return self.delegate.invoke(*args, **kwargs)
        def stream(self, *args, **kwargs):
            with closing(self.delegate.stream(*args, **kwargs)) as chunks:
                for chunk in chunks:
                    self.raw_stream += chunk.content
                    yield chunk

    model = RecordingModel(create_scoring_model())
    prompt = Path(__file__).resolve().parents[1] / "agent/prompts/report_generator.md"
    result = {"started_at": datetime.now(timezone.utc).isoformat(),
              "model": os.getenv("MODEL_NAME", "deepseek-v4-flash"),
              "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
              "sample_type": "synthetic S1-S8", "cases": [], "routes": []}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    for case in cases:
        if args.case_ids and case["id"] not in args.case_ids:
            continue
        model.raw_stream = ""
        started = time.perf_counter()
        metrics, chunks = [], []
        trace = EvaluationTrace(metrics.append)
        def emit(text):
            trace.content()
            chunks.append({"elapsed": round(time.perf_counter() - started, 4), "text": text})
        record = {**case, "metrics": metrics, "chunks": chunks}
        try:
            state = build_eval_graph(model, emit_chunk=emit, trace=trace).invoke({
                "resume_text": case["resume_text"], "user_message": case["user_message"], "page_count": 1})
            record["report_data"] = state.get("report_data")
            record["report"] = state.get("report")
            record["stream_matches_report"] = "".join(c["text"] for c in chunks) == state.get("report")
            record["engineering_outcome"] = "success" if state.get("report_data") else "needs_context"
        except Exception as error:
            record["engineering_outcome"] = "failed"
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            record["cause"] = str(error.__cause__) if error.__cause__ else None
            if getattr(error, "report", None):
                record["report"] = error.report
        record["raw_synthesis_stream"] = model.raw_stream
        record["elapsed"] = round(time.perf_counter() - started, 4)
        result["cases"].append(record)
        save()
        print(case["id"], record["engineering_outcome"], record["elapsed"], flush=True)

    if args.routes:
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
        from agent.service import load_system_prompt
        from agent.tools import analyze_resume, list_resumes, parse_resume_by_index
        # 同生产外层配置；只观察模型选择，不执行工具、数据库或联网搜索。
        router = init_chat_model(model="deepseek-v4-flash", max_tokens=4096, max_retries=0, timeout=60).bind_tools(
            [analyze_resume, list_resumes, parse_resume_by_index])
        pending = {"file_id": "synthetic-file", "missing_fields": ["application_type"]}
        tests = [
            ("明确分析", {"question": "分析这份简历，投后端校招", "file_id": "synthetic-file"}, True, False),
            ("续评", {"question": "校招", "pending_analysis": pending}, True, False),
            ("概念解释", {"question": "校招是什么意思", "pending_analysis": pending}, False, False),
            ("取消", {"question": "先不分析了", "pending_analysis": pending}, False, False),
            ("解释建议", {"question": "为什么建议补验证方法"}, False, True),
            ("改目标", {"question": "按前端校招重新分析这份简历"}, True, True),
            ("纠错", {"question": "你漏看了实习里的 Redis"}, False, True),
        ]
        for name, message, expected, has_report in tests:
            history = [SystemMessage(content=load_system_prompt())]
            if has_report:
                history += [HumanMessage(content="分析这份简历"), AIMessage(content="", tool_calls=[{
                    "name": "analyze_resume", "args": {"file_id": "synthetic-file"}, "id": "synthetic-call"}]),
                    ToolMessage(content="总体判断：检索项目有后端线索。A1：建议补充项目验证方法。原文：Python检索接口。", tool_call_id="synthetic-call")]
            elif "pending_analysis" in message:
                history += [HumanMessage(content="帮我分析简历，投后端"), AIMessage(content="已暂存解析结果，你这次是实习、校招还是社招？")]
            history.append(HumanMessage(content=str(message)))
            started = time.perf_counter()
            try:
                reply = router.invoke(history)
                names = [tool["name"] for tool in reply.tool_calls]
                record = {"name": name, "message": message, "tool_calls": reply.tool_calls, "content": reply.content,
                          "usage": reply.usage_metadata, "expected_analysis": expected,
                          "passed": ("analyze_resume" in names) == expected}
            except Exception as error:
                record = {"name": name, "passed": False, "error_type": type(error).__name__}
            record["elapsed"] = round(time.perf_counter() - started, 4)
            result["routes"].append(record)
            save()
            print("route", name, record["passed"], flush=True)


if __name__ == "__main__":
    main()
