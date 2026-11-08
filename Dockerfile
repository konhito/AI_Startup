FROM python:3.12-slim
WORKDIR /srv
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --uid 10001 --create-home gateway && mkdir /srv/data && chown gateway:gateway /srv/data
COPY app ./app
COPY frontend ./frontend
USER gateway
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
