ARG BASE_IMAGE=python:3.12.12-slim-bookworm
FROM ${BASE_IMAGE}
RUN python -m pip install --no-cache-dir pip==25.3 twine==7.0.0
ENTRYPOINT ["python", "/scripts/nexus_admin.py"]
