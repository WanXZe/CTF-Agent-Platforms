# syntax=docker/dockerfile:1
# Offline release assets are downloaded from official projects and verified.
FROM wanxze/nc:latest AS toolchain
ENV DEBIAN_FRONTEND=noninteractive \
    GHIDRA_HOME=/opt/ghidra \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    TERM=xterm-256color \
    PYTHONUNBUFFERED=1

# HTTPS and normal certificate validation work with the temporary build proxy.
COPY sandbox-assets/bootstrap-ca.crt /tmp/ctf-bootstrap-ca.crt
RUN sed -i 's|http://archive|https://archive|g; s|http://security|https://security|g' /etc/apt/sources.list \
 && apt-get -o Acquire::https::CaInfo=/tmp/ctf-bootstrap-ca.crt -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
 && apt-get -o Acquire::https::CaInfo=/tmp/ctf-bootstrap-ca.crt install -y --no-install-recommends \
      ca-certificates python3 python3-pip python3-dev python3-venv \
      gcc g++ gcc-multilib g++-multilib make cmake pkg-config git curl wget \
      file binutils xxd less ripgrep jq unzip zip p7zip-full xz-utils bzip2 \
      gdb gdb-multiarch strace ltrace patchelf nasm netcat-openbsd socat \
      openssl libssl-dev libffi-dev libgmp-dev libmpfr-dev libmpc-dev \
      libfplll-dev libc6-dbg libimage-exiftool-perl \
      zbar-tools libzbar0 binwalk foremost steghide sleuthkit yara \
      poppler-utils qpdf tesseract-ocr ffmpeg imagemagick \
      tcpdump tshark openjdk-21-jdk-headless apktool \
 && rm -rf /var/lib/apt/lists/* /tmp/ctf-bootstrap-ca.crt

COPY sandbox-requirements.txt /opt/ctf-tools/requirements.txt
RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
 && python3 -m pip install --no-cache-dir -r /opt/ctf-tools/requirements.txt \
 && python3 -m pip check \
 && python3 -m pip freeze > /opt/ctf-tools/python-packages.lock

# pyinstxtractor-ng pins xdis and Crypto versions incompatible with decompilers.
RUN python3 -m venv /opt/pyinstxtractor-ng \
 && /opt/pyinstxtractor-ng/bin/pip install --no-cache-dir pyinstxtractor-ng==2026.7.3 \
 && /opt/pyinstxtractor-ng/bin/pip check \
 && /opt/pyinstxtractor-ng/bin/pip freeze > /opt/ctf-tools/pyinstxtractor-packages.lock \
 && ln -s /opt/pyinstxtractor-ng/bin/pyinstxtractor-ng /usr/local/bin/pyinstxtractor-ng

# PE emulation uses its own Unicorn fork; keep it separate from angr/pwntools.
RUN python3 -m venv /opt/unipacker \
 && /opt/unipacker/bin/pip install --no-cache-dir unipacker==1.0.8 \
 && /opt/unipacker/bin/pip check \
 && /opt/unipacker/bin/pip freeze > /opt/ctf-tools/unipacker-packages.lock \
 && ln -s /opt/unipacker/bin/unipacker /usr/local/bin/unipacker

FROM toolchain AS release-tools
COPY sandbox-assets/ /tmp/releases/
RUN cd /tmp/releases && sha256sum -c SHA256SUMS \
 && mkdir -p /out/bin /out/opt /out/radare2 \
 && tar -xJf upx-5.2.1-amd64_linux.tar.xz \
 && cp upx-5.2.1-amd64_linux/upx /out/bin/upx \
 && dpkg-deb -x radare2_6.2.2_amd64.deb /out/radare2 \
 && python3 -c "import zipfile; zipfile.ZipFile('ghidra_12.1.4_PUBLIC_20260921.zip').extractall('/out/opt'); zipfile.ZipFile('jadx-1.5.6.zip').extractall('/out/opt/jadx')" \
 && mv /out/opt/ghidra_12.1.4_PUBLIC /out/opt/ghidra \
 && chmod +x /out/bin/upx /out/opt/jadx/bin/* /out/opt/ghidra/ghidraRun /out/opt/ghidra/support/* /out/opt/ghidra/Ghidra/Features/Decompiler/os/linux_x86_64/*

FROM toolchain
COPY --from=release-tools /out/radare2/usr/ /usr/
COPY --from=release-tools /out/bin/upx /usr/local/bin/upx
COPY --from=release-tools /out/opt/ghidra/ /opt/ghidra/
COPY --from=release-tools /out/opt/jadx/ /opt/jadx/
COPY sandbox-assets/manifest.json /opt/ctf-tools/release-assets.json
COPY ghidra-decompile /usr/local/bin/ghidra-decompile
COPY DecompileToC.java /opt/ctf-tools/ghidra-scripts/DecompileToC.java
COPY sandbox-tools.md /opt/ctf-tools/README.md
COPY sandbox-smoke.py /opt/ctf-tools/smoke.py
RUN ldconfig \
 && ln -s /opt/ghidra/support/analyzeHeadless /usr/local/bin/analyzeHeadless \
 && ln -s /opt/jadx/bin/jadx /usr/local/bin/jadx \
 && printf '#!/bin/sh\ncat /opt/ctf-tools/README.md\n' > /usr/local/bin/ctf-tools \
 && chmod +x /usr/local/bin/ghidra-decompile /usr/local/bin/ctf-tools \
 && dpkg-query -W > /opt/ctf-tools/apt-packages.lock \
 && r2 -v && upx --version && jadx --version

WORKDIR /workspace
CMD ["sleep", "infinity"]
