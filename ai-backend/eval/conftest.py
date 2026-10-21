"""离线收集不读取 .env、真实数据、模型 SDK；所有 DB 位于临时目录。"""
import os
import tempfile

_DATA = tempfile.TemporaryDirectory(prefix="guidance-tests-")
os.environ["APP_DATA_DIR"] = _DATA.name
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
