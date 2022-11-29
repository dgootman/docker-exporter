# syntax=docker/dockerfile:1

# Based on https://www.docker.com/blog/containerized-python-development-part-1/

FROM python:slim AS builder
RUN pip install poetry
COPY pyproject.toml ./
RUN poetry export -f requirements.txt --output requirements.txt

FROM python:slim
WORKDIR /app
COPY --from=builder requirements.txt .
RUN pip install -r requirements.txt
COPY *.py .

CMD [ "python3", "docker_exporter.py" ]
EXPOSE 8080
