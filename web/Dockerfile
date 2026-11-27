# Container for Render, Fly or Hugging Face Spaces (Docker).
FROM python:3.12-slim
WORKDIR /app
COPY *.py data_sources.json /app/
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt
COPY web/*.py /app/web/
COPY web/static /app/web/static
RUN mkdir -p /app/candle_cache && chmod 777 /app/candle_cache
ENV HOST=0.0.0.0 PORT=10000 TRUST_PROXY=1
EXPOSE 10000
CMD ["python", "/app/web/app.py"]
