# Not deployed. Example container for Hugging Face Spaces (Docker SDK), Render or Fly.
FROM python:3.12-slim
WORKDIR /app
COPY tester.py insights.py /app/
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt
COPY web/app.py /app/web/app.py
COPY web/static /app/web/static
RUN mkdir -p /app/candle_cache && chmod 777 /app/candle_cache
ENV HOST=0.0.0.0 PORT=7860
EXPOSE 7860
CMD ["python", "/app/web/app.py"]
