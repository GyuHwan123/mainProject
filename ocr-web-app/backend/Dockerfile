FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
<<<<<<< HEAD

=======
>>>>>>> ec0ab5c98ce6eaed656147372cd55aaeeb12a664
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]