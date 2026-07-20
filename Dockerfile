FROM python:3.11-slim

WORKDIR /app

# 优先用依赖缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 应用监听 8080；HOST 由 app.py 内写死 0.0.0.0
EXPOSE 8080

# 启动即拉起 Flask + 每5分钟自动全量刷新后台线程
CMD ["python", "app.py"]
