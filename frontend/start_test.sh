#!/bin/bash

# Kill any existing processes on the ports
echo "Cleaning up existing processes..."
lsof -ti:8000 | xargs kill -9 2>/dev/null
lsof -ti:5173 | xargs kill -9 2>/dev/null

# Start backend with mock LLM
echo "Starting backend server..."
export LLM_MOCK=1
export GLOBAL_INDEX_PATH=/Users/allenygy/Project/GAgent/INDEX.md
python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# Wait for backend to start
echo "Waiting for backend to start..."
sleep 3

# Start frontend
echo "Starting frontend server..."
cd frontend
npm run dev &
FRONTEND_PID=$!

echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"

echo ""
echo "Servers are running:"
echo "Backend: http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for interrupt
trap "kill $BACKEND_PID $FRONTEND_PID; exit" INT
wait
