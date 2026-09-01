FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
<<<<<<< HEAD
=======

>>>>>>> e662b86532de6e6d04cb211cd5d59158182161fe
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]