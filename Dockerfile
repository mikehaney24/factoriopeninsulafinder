FROM --platform=linux/amd64 debian:bookworm-slim

# Install dependencies: Factorio prerequisites and Python packages
RUN apt-get update && apt-get install -y \
    wget \
    xz-utils \
    ca-certificates \
    python3 \
    python3-pil \
    python3-tqdm \
    && rm -rf /var/lib/apt/lists/*

# Download and extract the Factorio headless server
WORKDIR /opt
RUN wget -O factorio_headless.tar.xz "https://factorio.com/get-download/experimental/headless/linux64" \
    && tar -xJf factorio_headless.tar.xz \
    && rm factorio_headless.tar.xz

# Set up the working directory for our script
WORKDIR /app
COPY find_peninsula.py .

# The entrypoint locks in the python script execution and hardcodes the container's Factorio path
ENTRYPOINT ["python3", "find_peninsula.py", "--factorio-bin", "/opt/factorio/bin/x64/factorio", "--out-dir", "/app/seed_previews"]
