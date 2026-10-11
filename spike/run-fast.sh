#!/bin/bash
# Wait for the qwen3 index to finish, then index the same corpus with the fast (NLP) method.
cd "$(dirname "$0")" && source .venv/bin/activate
while pgrep -f "graphrag index --root rag-qwe[n]" >/dev/null; do sleep 30; done
curl -s localhost:11434/api/generate -d '{"model":"qwen3-30b-a3b-16k","keep_alive":0}' >/dev/null
s=$(date +%s); echo "start $(date -Is)"
graphrag index --root rag-fast --method fast > rag-fast.log 2>&1
echo "exit $? in $(( $(date +%s)-s )) s"
