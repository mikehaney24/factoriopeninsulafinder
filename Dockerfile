FROM --platform=linux/amd64 debian:bookworm-slim

# Install wget and xz-utils to download and extract the Factorio headless binary
RUN apt-get update && apt-get install -y \
    wget \
    xz-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Download and extract the Factorio headless server
WORKDIR /opt
RUN wget -O factorio_headless.tar.xz "https://factorio.com/get-download/experimental/headless/linux64" \
    && tar -xJf factorio_headless.tar.xz \
    && rm factorio_headless.tar.xz

# Install uv by copying the binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set up the working directory for our script
WORKDIR /app
COPY find_peninsula.py .

# Pre-warm uv cache (downloads python & packages into image layer)
RUN uv run find_peninsula.py --help

# The entrypoint locks in the python script execution and hardcodes the container's Factorio path
ENTRYPOINT ["uv", "run", "find_peninsula.py", "--factorio-bin", "/opt/factorio/bin/x64/factorio", "--out-dir", "/app/seed_previews"]
