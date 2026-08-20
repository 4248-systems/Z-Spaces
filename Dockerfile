FROM python:3.10

WORKDIR /usr/src/app

COPY runner-requirements.txt .
RUN apt update && apt install -y --no-install-recommends build-essential cmake git curl wget libffi-dev libssl-dev pkg-config && \
    apt clean && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r runner-requirements.txt

EXPOSE 7860
ENV GRADIO_SERVER_NAME="0.0.0.0"

CMD ["python", "__main__.py"]