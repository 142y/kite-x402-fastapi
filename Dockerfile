# Multi-stage build: install the package into a throwaway prefix, then copy
# only the result into the runtime image. Keeps pip, setuptools and the
# build tools out of the final layer.
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /install /usr/local
# Everything is configurable through the environment; nothing is baked in.
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
# Run as the non-root user the base image ships with.
USER nobody
CMD ["python", "-m", "kite_x402"]
