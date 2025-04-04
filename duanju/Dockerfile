# 使用更小的 Alpine 镜像
FROM python:3.10-alpine

WORKDIR /app

# 安装依赖（最小化大小，移除编译器和构建工具）
COPY requirements.txt /app/requirements.txt
RUN apk add --no-cache gcc musl-dev libffi-dev zlib-dev jpeg-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev libffi-dev

COPY . /app

EXPOSE 5000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
