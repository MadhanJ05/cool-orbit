# Cloud Run image for the dashboard: static page + dashboard/server.py (standard library only).
# Build context = the dashboard/ folder (see DEPLOY.md). Exports are read from Cloud Storage at runtime, never baked in.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY index.html app.js style.css server.py ga3d.js ./
COPY vendor/three ./vendor/three
RUN useradd --uid 10001 --no-create-home web
USER web
EXPOSE 8080
CMD ["python", "server.py"]
