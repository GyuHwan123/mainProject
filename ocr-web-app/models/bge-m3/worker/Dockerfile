FROM ocr-web-app-backend:latest

RUN python -m pip install --no-cache-dir \
    pypdf==6.0.0 \
    pymupdf==1.28.2

WORKDIR /worker

CMD ["sleep", "infinity"]
