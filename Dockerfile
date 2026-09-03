FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qbank/ qbank/
COPY scripts/ scripts/
COPY tests/ tests/
COPY books.json FORMAT.md ./

# book PDFs: attach a Railway Volume mounted at /pdfs (or bake into
# pdfs/) and run:
#   python -m qbank run --book BIO
#   python -m qbank export
# NOTE: no Docker VOLUME instruction — Railway rejects it; mount
# Railway Volumes at /pdfs and /out from the dashboard instead.
ENV QBANK_PDFS_DIR=/pdfs \
    OUTPUT_DIR=/out

CMD ["python", "-m", "qbank", "--help"]
