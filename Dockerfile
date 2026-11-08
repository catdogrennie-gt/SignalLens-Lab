FROM python:3.12.10-slim
WORKDIR /work
COPY vendor /vendor
COPY requirements.txt .
RUN pip install --no-index --find-links=/vendor -r requirements.txt && rm -rf /vendor
COPY lab ./lab
RUN mkdir -p /work/.runtime/private /work/.runtime/samples /work/.runtime/runs
ENV PYTHONUNBUFFERED=1
