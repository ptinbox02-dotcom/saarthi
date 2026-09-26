# The lesson videos ship inside the image — ~200 MB, unremarkable for a container and
# much simpler than putting four MP4s behind object storage for a preview.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Hugging Face Spaces run the container as uid 1000. Everything the app touches has to
# be readable by that user, and the audit log has to go somewhere it can write.
RUN useradd -m -u 1000 saarthi
WORKDIR /app

COPY --chown=saarthi classroom/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# core/ holds model resolution and retry — the only code the app and the factory share.
COPY --chown=saarthi core ./core
COPY --chown=saarthi classroom/server ./classroom/server
COPY --chown=saarthi classroom/web ./classroom/web
COPY --chown=saarthi classroom/lessons ./classroom/lessons
COPY --chown=saarthi classroom/media ./classroom/media

USER saarthi
ENV PYTHONPATH=/app/core PORT=8080 SAARTHI_AUDIT_DIR=/tmp/saarthi-audit
EXPOSE 8080
CMD ["python", "classroom/server/app.py"]
