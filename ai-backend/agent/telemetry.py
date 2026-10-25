"""单次评分的安全性能指标：不记录简历、岗位、模型正文或异常正文。"""

import threading
import time
import uuid

from common.logger import get_agent_logger


class EvaluationTrace:
    def __init__(self, observer=None):
        self.run_id = uuid.uuid4().hex
        self.started = time.perf_counter()
        self.observer = observer
        self._lock = threading.Lock()
        self._attempts = {}
        self._first_content = False

    def record(self, event, **fields):
        metric = {"run_id": self.run_id, "event": event, **fields}
        get_agent_logger().info(0, "resume_performance", "评分性能", metric)
        if self.observer:
            self.observer(metric)

    def run(self, name, function):
        with self._lock:
            attempt = self._attempts.get(name, 0) + 1
            self._attempts[name] = attempt
        started = time.perf_counter()
        outcome = "success"
        error_type = None
        try:
            return function()
        except Exception as error:
            outcome, error_type = "error", type(error).__name__
            raise
        finally:
            self.record("node", node=name, attempt=attempt, outcome=outcome,
                        error_type=error_type, elapsed_seconds=round(time.perf_counter() - started, 4))

    def usage(self, name, response):
        usage = getattr(response, "usage_metadata", None) or {}
        self.record("model_usage", node=name,
                    input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    reasoning_tokens=(usage.get("output_token_details") or {}).get("reasoning"))

    def content(self):
        with self._lock:
            if self._first_content:
                return
            self._first_content = True
        self.record("first_content", elapsed_seconds=round(time.perf_counter() - self.started, 4))

    def finish(self, outcome):
        self.record("total", outcome=outcome,
                    elapsed_seconds=round(time.perf_counter() - self.started, 4))
