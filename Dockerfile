FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORE_DATA=/data/ocstore

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY store_server.py .
COPY web ./web
COPY samples ./samples

# /data 建议挂载为持久卷，否则容器重启会丢失上传的主题
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "store_server.py"]
