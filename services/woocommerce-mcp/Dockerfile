# WooCommerce MCP server image (small, non-root)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Make code readable by the non-root runtime user, whatever permissions the files had on the host
RUN chmod -R a+rX /app

# Run as an unprivileged user (matches runAsUser in the Helm chart)
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8080
CMD ["python", "-m", "app.server"]
