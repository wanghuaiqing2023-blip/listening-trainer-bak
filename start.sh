#!/bin/bash
# Start both backend and frontend with a single command

set -e
cd "$(dirname "$0")"

echo "=== 听力训练系统启动 ==="

# Check .env
if [ ! -f .env ]; then
  echo "⚠️  未找到 .env 文件，请先复制 .env.example 并填入 API Keys："
  echo "    cp .env.example .env"
  exit 1
fi

# Check Python venv
if [ ! -d .venv ]; then
  echo "📦 创建 Python 虚拟环境..."
  python -m venv .venv
fi

source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate

echo "📦 安装 Python 依赖..."
pip install -r requirements.txt -q

# Download spaCy model if not present
python -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null || \
  python -m spacy download en_core_web_sm

echo "🚀 启动后端 (http://localhost:8000)..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Frontend
cd frontend
if [ ! -d node_modules ]; then
  echo "📦 安装前端依赖..."
  npm install
fi

echo "🚀 启动前端 (http://localhost:5173)..."
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ 系统已启动："
echo "   前端: http://localhost:5173"
echo "   后端 API: http://localhost:8000"
echo "   API 文档: http://localhost:8000/docs"
echo ""
echo "按 Ctrl+C 停止"

# Wait for either process to exit
wait $BACKEND_PID $FRONTEND_PID
