FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Fuente DejaVu para el PDF de la conversación (tildes, ñ y símbolos técnicos)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Usuario sin privilegios; ANDES genera su código numérico una sola vez en el build
RUN useradd --create-home app
USER app
RUN andes prepare -q

COPY --chown=app:app src ./src
COPY --chown=app:app data ./data
COPY --chown=app:app tests ./tests

EXPOSE 8080
CMD exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --timeout-keep-alive 75
