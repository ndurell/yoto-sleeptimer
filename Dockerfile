FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    LOGIN_BIND=0.0.0.0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chmod=644 yoto_sleeptimer/ ./yoto_sleeptimer/
RUN chmod 755 /app/yoto_sleeptimer

# No secrets in the image: the client ID comes from the environment and the
# refresh token lives in the /data volume.
VOLUME /data
ENTRYPOINT ["python", "-m", "yoto_sleeptimer"]
CMD ["run"]
