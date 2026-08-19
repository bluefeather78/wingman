#!/bin/bash
cd "/c/Users/shama/Documents/wingman"

echo "[1] Clearing cache..."
python clear_deadline_cache.py

echo "[2] Starting server..."
timeout 20 python server.py 2>&1 &
SERVER_PID=$!

echo "Server PID: $SERVER_PID"
sleep 3

echo "[3] Making request..."
curl -v "http://localhost:8000/api/opportunities/ec12081/deadline" 2>&1 | head -30

echo "[4] Waiting..."
sleep 2

echo "[5] Killing server..."
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null || true

echo "[6] Done"
