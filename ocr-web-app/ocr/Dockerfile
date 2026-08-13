FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        libglib2.0-0 \
        libgl1 \
        libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY spreadsheet-requirements.txt .
RUN python -c "from pathlib import Path; lines = Path('requirements.txt').read_text(encoding='utf-16').splitlines(); Path('requirements-docker.txt').write_text('\\n'.join(line for line in lines if not line.startswith(('docx2pdf==', 'pywin32=='))) + '\\n', encoding='utf-8')" \
    && pip install --no-cache-dir -r requirements-docker.txt -r spreadsheet-requirements.txt

COPY . .

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
