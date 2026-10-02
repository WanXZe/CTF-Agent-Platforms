"""Offline end-to-end checks for the CTF sandbox toolchain."""
import importlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(*args, timeout=60, cwd=None):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args!r}\n{result.stdout[-8000:]}")
    return result.stdout


def main():
    checks = []
    modules = ["Crypto", "gmpy2", "z3", "sympy", "PIL", "numpy", "pyzbar.pyzbar", "pypdf",
               "pwn", "angr", "claripy", "capstone", "unicorn", "lief", "pefile", "elftools",
               "ropgadget", "ropper", "r2pipe", "keystone", "uncompyle6", "decompyle3", "frida",
               "yara", "magic", "scapy.all", "dpkt", "construct", "bitstring", "cryptography",
               "fpylll", "cysignals", "cv2", "pytesseract", "qrcode", "py7zr"]
    for name in modules:
        importlib.import_module(name)
    run("python3", "-m", "pip", "check")
    checks.append(f"{len(modules)} Python modules and pip check")
    commands = ["upx", "r2", "rabin2", "rasm2", "gdb", "gdb-multiarch", "strace", "ltrace",
                "patchelf", "nasm", "ghidra-decompile", "analyzeHeadless", "jadx", "apktool",
                "pyinstxtractor-ng", "unipacker", "uncompyle6", "decompyle3", "ROPgadget", "ropper", "pwn",
                "frida", "frida-trace", "7z", "binwalk", "foremost", "steghide", "mmls", "fls",
                "exiftool", "yara", "pdftotext", "qpdf", "zbarimg", "tesseract", "ffmpeg",
                "convert", "tshark", "tcpdump", "rg", "jq", "ctf-tools"]
    for command in commands:
        if not shutil.which(command):
            raise RuntimeError(f"Missing executable: {command}")
    versions = {"upx": run("upx", "--version").splitlines()[0],
                "radare2": run("r2", "-v").splitlines()[0],
                "jadx": run("jadx", "--version").strip()}
    run("pyinstxtractor-ng", "--help")
    run("/opt/unipacker/bin/python", "-c", "import unipacker.core, unipacker.shell; import unicorn; assert unicorn.__version__")
    run("binwalk", "--help")
    run("tshark", "--version")
    checks.append(f"{len(commands)} executables and CLI startup")

    with tempfile.TemporaryDirectory(prefix="ctf-smoke-") as tmp:
        root = Path(tmp)
        source = root / "sample.c"
        source.write_text('#include <stdio.h>\nint main(int n, char **v) { if (n > 1 && v[1][0]==67 && v[1][1]==84 && v[1][2]==70) puts("CTF_TOOL_OK"); else puts("NO"); return 0; }\n')
        binary = root / "sample"
        run("gcc", "-O0", "-fno-pie", "-no-pie", "-o", str(binary), str(source))
        binary32 = root / "sample32"
        run("gcc", "-m32", "-o", str(binary32), str(source))
        assert "CTF_TOOL_OK" in run(str(binary32), "CTF")
        checks.append("32-bit and 64-bit compilation and execution")

        packed = root / "packed"
        unpacked = root / "unpacked"
        run("upx", "--best", "-o", str(packed), str(binary))
        run("upx", "-t", str(packed))
        run("upx", "-d", "-o", str(unpacked), str(packed))
        assert "CTF_TOOL_OK" in run(str(unpacked), "CTF")
        checks.append("UPX pack, test, unpack and execute")
        assert "main" in run("r2", "-q", "-c", "aaa; afl", str(binary))
        assert "CTF_TOOL_OK" in run("rabin2", "-z", str(binary))
        assert "exited normally" in run("gdb", "-q", "-batch", "-ex", "run CTF", str(binary))
        checks.append("radare2 analysis and GDB inferior execution")

        import angr
        import claripy
        project = angr.Project(str(binary), auto_load_libs=False)
        arg = claripy.BVS("arg", 3 * 8)
        state = project.factory.entry_state(args=[str(binary), arg],
            add_options={angr.options.ZERO_FILL_UNCONSTRAINED_MEMORY, angr.options.ZERO_FILL_UNCONSTRAINED_REGISTERS})
        manager = project.factory.simgr(state)
        manager.explore(find=lambda s: b"CTF_TOOL_OK" in s.posix.dumps(1),
                        avoid=lambda s: b"NO\n" in s.posix.dumps(1))
        assert manager.found and manager.found[0].solver.eval(arg, cast_to=bytes) == b"CTF"
        checks.append("angr symbolic execution recovers expected input")

        pseudocode = root / "sample.c.out"
        run("ghidra-decompile", str(binary), str(pseudocode), timeout=300)
        assert "main" in pseudocode.read_text() and "CTF_TOOL_OK" in pseudocode.read_text()
        checks.append("Ghidra headless analysis and C pseudocode export")
        java = root / "SandboxHello.java"
        java.write_text('public class SandboxHello { public static void main(String[] args) { System.out.println("JADX_TOOL_OK"); } }\n')
        run("javac", "--release", "11", str(java))
        decompiled = root / "jadx-output"
        run("jadx", "-d", str(decompiled), str(root / "SandboxHello.class"), timeout=120)
        assert any("JADX_TOOL_OK" in p.read_text() for p in decompiled.rglob("*.java"))
        checks.append("JADX Java bytecode decompilation")

        payload = root / "payload.py"
        payload.write_text('print("PYINSTALLER_TOOL_OK")\n')
        run("python3", "-m", "PyInstaller", "--onefile", "--noupx", "--distpath", str(root / "dist"),
            "--workpath", str(root / "build"), "--specpath", str(root), str(payload), timeout=180, cwd=root)
        executable = root / "dist" / "payload"
        assert "PYINSTALLER_TOOL_OK" in run(str(executable))
        run("pyinstxtractor-ng", str(executable), timeout=120, cwd=root)
        assert any(b"PYINSTALLER_TOOL_OK" in p.read_bytes() for p in root.rglob("payload.pyc"))
        checks.append("PyInstaller build, execution and bundled bytecode extraction")

        import qrcode
        from pyzbar.pyzbar import decode
        image = qrcode.make("CTF_QR_OK")
        assert decode(image.convert("RGB"))[0].data == b"CTF_QR_OK"
        checks.append("QR encode and decode")
        from scapy.all import IP, UDP, Raw, wrpcap
        pcap = root / "sample.pcap"
        wrpcap(str(pcap), [IP(src="10.1.2.3", dst="10.2.3.4") / UDP(sport=1234, dport=5678) / Raw(b"CTF_PACKET_OK")])
        assert "10.1.2.3" in run("tshark", "-r", str(pcap), "-T", "fields", "-e", "ip.src")
        checks.append("PCAP generation and tshark packet inspection")
    report = {"passed": True, "versions": versions, "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
