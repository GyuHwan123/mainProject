#!/bin/sh
set -eu

ollama serve &
server_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

if ! ollama list | grep -q '^gemma2:2b'; then
  echo "Preparing gemma2:2b model..."
  ollama pull gemma2:2b
fi

wait "$server_pid"
