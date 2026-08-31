FROM ollama/ollama:latest

WORKDIR /root

COPY entrypoint.sh /entrypoint.sh
# Git on Windows may check out shell scripts with CRLF. Normalize it inside the
# Linux image so the shebang always resolves to /bin/sh.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
