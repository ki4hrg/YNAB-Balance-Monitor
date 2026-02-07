FROM python:3.13-alpine
WORKDIR /app
COPY monitor.py .
CMD ["python", "monitor.py"]
