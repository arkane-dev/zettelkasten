#!/bin/bash
# Wait for the gemma3 index to finish, then index the same corpus with qwen3.
cd "$(dirname "$0")" && source .venv/bin/activate
while pgrep -f "graphrag index --root ra[g]$" >/dev/null; do sleep 30; done
curl -s localhost:11434/api/generate -d '{"model":"gemma3-27b-16k","keep_alive":0}' >/dev/null
s=$(date +%s); echo "start $(date -Is)"
graphrag index --root rag-qwen > rag-qwen.log 2>&1
echo "exit $? in $(( $(date +%s)-s )) s"
