FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
RUN pip install -r requirements-postgres.txt && groupadd -g 10001 logentra \
    && useradd -u 10001 -g 10001 -M logentra && install -d -o 10001 -g 10001 /app/data
COPY run.py alembic.ini ./
COPY logentra_server/ logentra_server/
COPY migrations/ migrations/
COPY ui/Logentra.html ui/Logentra.html
COPY scripts/recovery.py scripts/verify_evidence.py scripts/
USER 10001:10001
EXPOSE 8787
ENV LOGENTRA_PROFILE=production LOGENTRA_HOST=0.0.0.0
CMD ["python", "run.py", "--no-browser"]
