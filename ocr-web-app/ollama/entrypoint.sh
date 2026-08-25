#!/bin/sh
set -eu

ollama serve &
server_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

llm_model="${OLLAMA_LLM_MODEL:-gemma2:2b}"
receipts_model="${OLLAMA_RECEIPTS_MODEL:-}"
embedding_model="${OLLAMA_EMBEDDING_MODEL:-embeddinggemma}"

for model in "$llm_model" "$receipts_model" "$embedding_model"; do
  if [ -z "$model" ]; then
    continue
  fi
  if ! ollama list | grep -Fq "$model"; then
    echo "Preparing $model model..."
    ollama pull "$model"
  fi
done

wait "$server_pid"
