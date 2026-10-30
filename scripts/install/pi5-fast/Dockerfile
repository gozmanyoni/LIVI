FROM debian:trixie-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates dosfstools e2fsprogs fdisk file jq kmod openssh-client \
    parted python3 rsync shellcheck squashfs-tools systemd udev util-linux xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /builder
