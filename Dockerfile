FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ libc6-dev && rm -rf /var/lib/apt/lists/*
COPY src/main.cpp /src/main.cpp
RUN g++ -O3 -DNDEBUG -std=c++17 -pthread -march=x86-64-v3 /src/main.cpp -o /sessions

FROM python:3.12-slim-bookworm
RUN useradd --uid 1000 --no-create-home --home-dir /scratch app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY --from=build /sessions /app/build/sessions
COPY pipeline/ /app/pipeline/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
CMD ["python3", "/app/pipeline/pipeline.py"]
