#!/bin/sh
set -eu

ollama serve &
server_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

llm_model="${OLLAMA_LLM_MODEL:-gemma2:2b}"
receipts_model="${OLLAMA_RECEIPTS_MODEL:-}"
receipts_modelfile="${OLLAMA_RECEIPTS_MODELFILE:-}"
rag_model="${OLLAMA_RAG_MODEL:-}"
rag_gguf_path="${OLLAMA_RAG_GGUF_PATH:-}"

for model in "$llm_model"; do
  if ! ollama list | grep -Fq "$model"; then
    echo "Preparing $model model..."
    ollama pull "$model"
  fi
done

# Receipt extraction can use a local full-model GGUF through a Modelfile.
if [ -n "$receipts_model" ] || [ -n "$receipts_modelfile" ]; then
  if [ -z "$receipts_model" ] || [ -z "$receipts_modelfile" ]; then
    echo "OLLAMA_RECEIPTS_MODEL and OLLAMA_RECEIPTS_MODELFILE must be set together." >&2
    exit 1
  fi
  if [ ! -f "$receipts_modelfile" ]; then
    echo "Receipt Modelfile was not found: $receipts_modelfile" >&2
    exit 1
  fi
  if ! ollama list | awk 'NR > 1 { print $1 }' | grep -Fxq "$receipts_model"; then
    echo "Registering local receipt model $receipts_model from $receipts_modelfile..."
    ollama create "$receipts_model" -f "$receipts_modelfile"
  fi
fi

if [ -n "$rag_model" ] || [ -n "$rag_gguf_path" ]; then
  if [ -z "$rag_model" ] || [ -z "$rag_gguf_path" ]; then
    echo "OLLAMA_RAG_MODEL and OLLAMA_RAG_GGUF_PATH must be set together." >&2
    exit 1
  fi
  if [ ! -f "$rag_gguf_path" ]; then
    echo "GGUF model was not found: $rag_gguf_path" >&2
    exit 1
  fi
  if ! ollama list | awk 'NR > 1 { print $1 }' | grep -Fxq "$rag_model"; then
    echo "Registering local RAG model $rag_model from $rag_gguf_path..."
    modelfile="$(mktemp)"
    printf 'FROM %s\n' "$rag_gguf_path" > "$modelfile"
    ollama create "$rag_model" -f "$modelfile"
    rm -f "$modelfile"
  fi
fi

touch /tmp/ollama-models-ready

wait "$server_pid"
