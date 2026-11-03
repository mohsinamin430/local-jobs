FROM python:3.12-slim
RUN pip install --no-cache-dir 'httpx[http2]==0.28.1'
WORKDIR /app
ENV DATA_DIR=/data
COPY *.py ./
COPY config ./config
RUN useradd --uid 10001 --create-home collector && mkdir /data && chown collector:collector /data
USER collector
VOLUME ["/data"]
ENTRYPOINT ["python3", "/app/collector.py"]
CMD ["run", "--config", "/data/profile.json", "--data-dir", "/data"]
