FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY ai-backend/requirements-runtime.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt && python -m pip check
COPY ai-backend/main.py ai-backend/SystemPrompt.md /app/
COPY ai-backend/agent/ /app/agent/
COPY ai-backend/auth/ /app/auth/
COPY ai-backend/common/ /app/common/
COPY ai-backend/conversation/ /app/conversation/
COPY ai-backend/files/ /app/files/
COPY ai-backend/state/ /app/state/
# Existing persistent directories are root-owned. Drop capabilities and restrict writes
# in Compose; migrate ownership/non-root runtime separately, not during data upgrade.
RUN mkdir -p /app/agent/db /app/conversation/db /app/common/logs
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=6s --start-period=40s --retries=4 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)" || exit 1
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
