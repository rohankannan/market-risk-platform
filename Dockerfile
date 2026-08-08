FROM python:3.11-slim

# .git stays out of the build context, so risk_runs.code_version comes from
# this build arg (compose passes GIT_SHA through; empty means "unknown")
ARG GIT_SHA=
ENV GIT_SHA=${GIT_SHA} \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# one image for every role: api (default), dashboard, batch, scheduler -
# compose picks the command; keeps the demo to a single build
COPY . .
RUN pip install . && useradd --create-home riskdesk
USER riskdesk

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
