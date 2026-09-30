FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/
RUN useradd -m bess && mkdir /data && chown bess /data
USER bess
ENV BESS_HOST=0.0.0.0 BESS_PORT=8000 BESS_DB=/data/bess.db
EXPOSE 8000
CMD ["python3","server.py"]
