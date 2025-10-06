# Use a real tag (NOT 3.1)
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive

# System deps: R + headers for preventr
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        r-base libcurl4-openssl-dev libssl-dev libxml2-dev && \
    R -q -e "options(repos=c(CRAN='https://cloud.r-project.org')); install.packages('preventr', Ncpus=2)" && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# App deps
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app.py chd_estimate.R /app/

# Gradio tweaks
ENV GRADIO_ANALYTICS_ENABLED=False \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PORT=7860

EXPOSE 7860
CMD ["python", "app.py"]
