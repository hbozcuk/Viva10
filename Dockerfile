FROM python:3.10-slim
ENV DEBIAN_FRONTEND=noninteractive

# 1) Sistem bağımlılıkları: R + derleyici araçları + gerekli dev kütüphaneler
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        r-base r-base-dev \
        build-essential gfortran \
        libcurl4-openssl-dev libssl-dev libxml2-dev libicu-dev && \
    rm -rf /var/lib/apt/lists/*

# 2) R paket(ler)i: preventr (+ bağımlılıkları)
# dependencies=TRUE diyerek dplyr, rlang, vctrs vb. zinciri kurulur
RUN R -q -e "options(repos=c(CRAN='https://cloud.r-project.org'), Ncpus=2); \
             install.packages('preventr', dependencies=TRUE)"

# 3) Python bağımlılıkları
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 4) Uygulama dosyaları
COPY app.py chd_estimate.R /app/

# 5) Gradio / network
ENV GRADIO_ANALYTICS_ENABLED=False \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]
