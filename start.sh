#!/usr/bin/env bash
set -e

echo "============================================================"
echo "  Automated Documentation Drift Detection - Full Stack"
echo "============================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "🔍 Checking prerequisites..."

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "${RED}❌ Python 3 is not installed${NC}"
    exit 1
fi
echo "${GREEN}✅ Python 3 found${NC}"

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "${RED}❌ Node.js is not installed${NC}"
    exit 1
fi
echo "${GREEN}✅ Node.js found${NC}"

# Check Ollama (optional)
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "${GREEN}✅ Ollama is running at http://localhost:11434${NC}"
else
    echo "${YELLOW}⚠️  Ollama is not running on port 11434${NC}"
    echo "${YELLOW}   The system will work with offline fallback (heuristic detection)${NC}"
    echo "${YELLOW}   To enable LLM features, install Ollama and run: ollama pull gemma4:e4b nomic-embed-text${NC}"
fi

echo ""
echo "📦 Installing backend dependencies..."
cd backend
.venv/bin/pip install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt 2>/dev/null
.venv/bin/pip install -q fastapi uvicorn httpx aiosqlite 2>/dev/null || pip install -q fastapi uvicorn httpx aiosqlite 2>/dev/null
cd ..

echo ""
echo "📦 Installing frontend dependencies..."
npm install 2>/dev/null || true

echo ""
echo "⚡ Launching FastAPI backend on http://localhost:8000..."
cd backend
.venv/bin/python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

echo ""
echo "💻 Launching Next.js dashboard on http://localhost:3000..."
npm run dev &
FRONTEND_PID=$!

echo ""
echo "============================================================"
echo "${GREEN}✅ System started!${NC}"
echo ""
echo "   FastAPI Backend:  http://localhost:8000"
echo "   API Docs:         http://localhost:8000/docs"
echo "   Next.js Frontend: http://localhost:3000"
echo "   Health Check:     http://localhost:8000/api/health"
echo ""
echo "   Backend PID: $BACKEND_PID"
echo "   Frontend PID: $FRONTEND_PID"
echo ""
echo "   Press Ctrl+C to stop both services."
echo "============================================================"

# Trap signals to cleanly terminate both processes
trap "echo ''; echo '🛑 Stopping services...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '✅ Services stopped.'" EXIT INT TERM

wait
