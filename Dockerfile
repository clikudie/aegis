FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir pywebostv

COPY app.py /app/app.py
COPY static /app/static
COPY scripts /app/scripts

ENV HOST=0.0.0.0 \
    PORT=8787 \
    STATE_FILE=/data/state.json \
    LG_TV_KEY_FILE=/data/lgtv-key.json

EXPOSE 8787

CMD ["python", "/app/app.py"]
