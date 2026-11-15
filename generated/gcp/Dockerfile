# Use official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.12-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED=1

# Copy local code to the container image.
ENV APP_HOME=/app
WORKDIR $APP_HOME
COPY main.py ./

# Run the web service on container startup.
# We run the built-in http.server directly using main.py.
CMD ["python", "main.py"]
