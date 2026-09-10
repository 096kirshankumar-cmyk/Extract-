FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qbank/ qbank/
COPY scripts/ scripts/
COPY tests/ tests/
COPY review_dashboard/ review_dashboard/
COPY books.json FORMAT.md dashboard.py ./

# book PDFs: attach a Railway Volume mounted at /pdfs (or bake into
# pdfs/) and run:
#   python -m qbank run --book BIO
#   python -m qbank export
# NOTE: no Docker VOLUME instruction — Railway rejects it; mount
# Railway Volumes at /pdfs and /out from the dashboard instead.
ENV QBANK_PDFS_DIR=/pdfs \
    OUTPUT_DIR=/out

# Railway injects $PORT; the dashboard binds 0.0.0.0:$PORT
CMD ["python", "dashboard.py"]

# Optional Gemini table-vision pass: set GEMINI_API_KEYS (comma separated,
# keys from SEPARATE Google projects — quota is per project) or
# GEMINI_API_KEY_1..20 / GEMINI_API_KEY. Without keys the pipeline stays
# deterministic and the receipt reports llm_tables_repaired: 0 honestly.
