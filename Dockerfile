# One container: web app, background jobs and both checking models. Port 7860 is what Hugging Face Spaces expects.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 HF_HOME=/app/.hf BATON_DATA_DIR=/data TOKENIZERS_PARALLELISM=false

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# Models are baked into the image, so a sleeping Space wakes without downloading them again.
RUN python -c "from transformers import AutoModel, AutoModelForSequenceClassification as C, AutoTokenizer as T; \
n='cross-encoder/nli-deberta-v3-base'; C.from_pretrained(n); T.from_pretrained(n); \
m='sentence-transformers/all-MiniLM-L6-v2'; AutoModel.from_pretrained(m); T.from_pretrained(m)"

COPY baton ./baton

RUN useradd --create-home --uid 1000 baton \
 && mkdir -p /data && chown -R baton:baton /data /app
USER baton

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:7860/healthz')"
CMD ["uvicorn", "baton.web.app:get_app", "--factory", "--host", "0.0.0.0", "--port", "7860"]
