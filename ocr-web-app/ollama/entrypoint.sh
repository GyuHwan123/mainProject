#!/bin/sh
set -eu

ollama serve &
server_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

llm_model="${OLLAMA_LLM_MODEL:-gemma2:2b}"
embedding_model="${OLLAMA_EMBEDDING_MODEL:-embeddinggemma}"

for model in "$llm_model" "$embedding_model"; do
  if ! ollama list | grep -Fq "$model"; then
    echo "Preparing $model model..."
    ollama pull "$model"
  fi
done

wait "$server_pid"
