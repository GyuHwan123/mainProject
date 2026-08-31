# Local RAG GGUF models

Put private or large QLoRA GGUF files in this directory. Git ignores every file
here except this guide. Docker Compose mounts this directory read-only at
`/models/rag` in the Ollama container.

See the repository README for registration through `OLLAMA_RAG_MODEL` and
`OLLAMA_RAG_GGUF_PATH`.
