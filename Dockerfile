FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qbank/ qbank/
COPY scripts/ scripts/
COPY tests/ tests/
COPY books.json FORMAT.md ./

# book PDFs: mount at /pdfs (or bake into pdfs/) and run:
#   python -m qbank run --book BIO
#   python -m qbank export
ENV QBANK_PDFS_DIR=/pdfs \
    OUTPUT_DIR=/out
VOLUME ["/pdfs", "/out"]

RUN python -m pytest tests/ -q

CMD ["python", "-m", "qbank", "--help"]
