FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/data/bot.db

WORKDIR /app

RUN addgroup --system --gid 10001 partyradar \
    && adduser --system --uid 10001 --ingroup partyradar --home /home/partyradar partyradar

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=partyradar:partyradar . .
RUN mkdir -p /data \
    && chown partyradar:partyradar /data

USER partyradar

CMD ["python", "bot.py"]
