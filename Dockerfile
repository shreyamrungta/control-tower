# Integrated Manufacturing Operations Control Tower
#
# Works on any container host (Render, Railway, Fly.io, Hugging Face Spaces,
# Azure Container Apps, a university server). No API keys, no external services,
# no database - the whole system is self-contained.
#
#   docker build -t control-tower .
#   docker run -p 8501:8501 control-tower
#
# Then open http://localhost:8501

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the dataset at image-build time so the first request is fast.
# generate_data.py uses a fixed seed, so every build produces identical numbers.
RUN python generate_data.py

# Most hosts inject the port to listen on via $PORT; default to 8501 locally.
ENV PORT=8501
EXPOSE 8501

# Health check so the host can tell a hung container from a starting one
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",8501)}/_stcore/health')"

CMD streamlit run dashboard/app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
