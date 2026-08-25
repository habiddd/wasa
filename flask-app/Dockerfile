FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# En desarrollo puedes usar "flask run"; en producción usa gunicorn (ya incluido).
# --timeout 120: el modelo local usado para extraer fecha/hora de una reserva
# (llm_client.extraer_datos_reserva) puede tardar hasta 90s en responder (es
# un modelo "razonador" que gasta tiempo pensando antes de contestar) -- con
# el timeout por defecto de gunicorn (30s) o el anterior (60s), el worker
# podía morir a mitad de una request de reserva antes de que el LLM alcanzara
# a responder.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "wsgi:app"]
