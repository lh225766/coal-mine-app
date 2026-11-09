FROM python:3.11-slim

WORKDIR /app

# 纯 Python 标准库，无需 pip install
COPY webapp.py coal_mine_mock_mcp.py webapp.html ./

# DevCloud 仅路由 8080 端口
EXPOSE 8080

# 应用已绑定 0.0.0.0:8080
CMD ["python", "webapp.py"]