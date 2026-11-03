# Starter image for projects whose verification uses Python and pytest.
# Add the project's reviewed dependencies before using it for a real project.
FROM python:3.12-slim
RUN python -m pip install --no-cache-dir pytest
WORKDIR /workspace
ENTRYPOINT []
