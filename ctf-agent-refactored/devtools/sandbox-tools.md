# CTF sandbox tools

Run `ctf-tools` to show this inventory. All tools run inside the sandbox.

| Task | Tools / example |
| --- | --- |
| File / packer triage | `file`, `strings`, `readelf`, `objdump`, `xxd`, `rabin2 -I ./binary` |
| UPX unpacking | `upx -t ./binary`; `upx -d ./binary -o unpacked` |
| Windows PE emulated unpacking | `unipacker` interactive shell (dedicated Unicorn environment); `help` for commands |
| Native disassembly | `r2 -A ./binary`, `rabin2`, `rasm2`, Python `r2pipe`, `capstone` |
| Native C pseudocode | `ghidra-decompile ./binary ./decompiled.c`; advanced: `analyzeHeadless` |
| Python bundled executables | `pyinstxtractor-ng ./program`; `uncompyle6` / `decompyle3` for supported bytecode versions |
| Java / Android | `jadx -d out app.apk`, `apktool d app.apk`, `javap -c Example.class` |
| Binary debugging | `gdb`, `gdb-multiarch`, `strace`, `ltrace`, `patchelf`, `nasm` |
| Pwn / emulation / symbolic execution | Python `pwn`, `angr`, `claripy`, `unicorn`, `keystone`; `ROPgadget`, `ropper`, `pwn checksec` |
| PE / ELF / signatures / instrumentation | Python `lief`, `pefile`, `elftools`, `yara`; `yara`, `frida`, `frida-trace` |
| Crypto / lattice | Python `Crypto`, `gmpy2`, `z3`, `sympy`, `cryptography`, `fpylll` |
| Archives / firmware / forensics | `7z`, `unzip`, `binwalk`, `foremost`, `steghide`, `exiftool`, `mmls`, `fls` |
| PDF / QR / images / audio | `pdftotext`, `qpdf`, `zbarimg`, `tesseract`, `ffmpeg`, `convert`; Python `pypdf`, `pyzbar`, `cv2`, `PIL` |
| Packet inspection | `tshark -r capture.pcap`, `tcpdump -r capture.pcap`; Python `scapy`, `dpkt` |
| Shell / network / scripting | `rg`, `jq`, `curl`, `wget`, `nc`, `socat`, `openssl`, `gcc`, `g++`, `make`, Python 3.10 |

UPX handles supported UPX-packed binaries. Unipacker adds PE emulation for supported packers; unknown packers and obfuscators may require manual analysis. Bytecode decompilation depends on the input Python version. Ghidra / JADX are available through command line; this image is intended for automated headless work.

GCC supports both `-m64` and `-m32`. Normal GDB launch and analysis work with default container permissions. Attaching to another process or Frida instrumentation may require explicitly granting `SYS_PTRACE` for that debugging session.

Installed versions: `/opt/ctf-tools/python-packages.lock`, `pyinstxtractor-packages.lock`, `unipacker-packages.lock`, `apt-packages.lock`, `release-assets.json`. PyInstaller extraction and PE unpacking use separate Python environments to keep their pinned dependencies away from the main analysis libraries.
Offline smoke check: `python3 /opt/ctf-tools/smoke.py`.
