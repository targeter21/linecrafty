#!/usr/bin/env python3
"""linecrafty setup: finds ComfyUI, downloads + verifies the models, installs the SVG tracer.

Safe to re-run any time (skips what is already done, resumes interrupted downloads).
Works on Windows, Linux and macOS with any Python 3.8+ (standard library only).

Usage: python setup.py [-y] [path/to/ComfyUI]
  -y, --yes     don't ask before downloading
  path          your ComfyUI folder (the one containing main.py, or its parent)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
HOME = Path.home()

MODELS = [
    dict(folder="diffusion_models", name="flux1-dev-kontext_fp8_scaled.safetensors", size=11904640136,
         sha256="630ba795ec64283b4230ea23cf79406c2c68b7c578229ed139f30043eadb30a2",
         url="https://huggingface.co/Comfy-Org/Flux-1-Kontext-Dev/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"),
    dict(folder="text_encoders", name="t5xxl_fp8_e4m3fn_scaled.safetensors", size=5157348688,
         sha256="a498f0485dc9536735258018417c3fd7758dc3bccc0a645feaa472b34955557a",
         url="https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors"),
    dict(folder="text_encoders", name="clip_l.safetensors", size=246144152,
         sha256="660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd",
         url="https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"),
    dict(folder="vae", name="ae.safetensors", size=335304388,
         sha256="afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38",
         url="https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors"),
    dict(folder="upscale_models", name="4x-AnimeSharp.pth", size=67010245,
         sha256="e7a7de2dafd7331c1992862bbbcd9e9712a9f9f8e6303f0aaa59b4341d359bab",
         url="https://huggingface.co/Kim2091/AnimeSharp/resolve/main/4x-AnimeSharp.pth"),
]

# Run inside ComfyUI's own Python to see what it can do.
CHECK_CODE = r'''
import json
r = {}
try:
    import torch
    r["torch"] = torch.__version__
    p = None
    if torch.cuda.is_available():  # NVIDIA (CUDA) and AMD (ROCm builds report through torch.cuda too)
        p = torch.cuda.get_device_properties(0)
        r["vendor"] = "AMD" if getattr(torch.version, "hip", None) else "NVIDIA"
    elif hasattr(torch, "xpu") and torch.xpu.is_available():
        p = torch.xpu.get_device_properties(0)
        r["vendor"] = "Intel"
    if p is not None:
        r["gpu"] = p.name; r["vram_gb"] = round(p.total_memory / 2**30, 1)
except Exception as e:
    r["torch_error"] = str(e)
for m in ("numpy", "PIL", "scipy"):
    try:
        __import__(m)
    except Exception:
        r.setdefault("missing", []).append(m)
print(json.dumps(r))
'''

# ------------------------------------------------------------------ output helpers

if IS_WINDOWS:
    os.system("")  # enables ANSI colors in the Windows console
_COLOR = sys.stdout.isatty()
YES = False


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def step(m): print("\n" + _c("36", f"== {m}"))
def ok(m): print("   " + _c("32", m))
def info(m=""): print("   " + m if m else "")
def warn(m): print("   " + _c("33", f"WARNING: {m}"))


class Stop(Exception):
    pass


def fail(m):
    raise Stop(m)


def prompt(q: str, default: str = "") -> str:
    try:
        a = input(f"   {q} ").strip().strip('"')
    except EOFError:
        a = ""
    return a or default


def ask(q: str, default: str = "y") -> bool:
    if YES:
        return True
    hint = "[Y/n]" if default == "y" else "[y/N]"
    return prompt(f"{q} {hint}", default).lower().startswith("y")


def gb(n: float) -> str:
    return f"{n / 2**30:.1f} GB"


# ------------------------------------------------------------------ 1. find ComfyUI

def resolve_comfy_dir(path: str | Path | None) -> Path | None:
    if not path or not str(path).strip():
        return None
    base = Path(os.path.expanduser(str(path).strip().strip('"')))
    for d in (base, base / "ComfyUI", base / "resources" / "ComfyUI"):
        if (d / "main.py").is_file() and (d / "comfy").is_dir():
            return d.resolve()
    return None


def python_rels() -> list[str]:
    sub, exe = ("Scripts", "python.exe") if IS_WINDOWS else ("bin", "python")
    return [f".venv/{sub}/{exe}", f"venv/{sub}/{exe}", f"../python_embeded/{exe}",
            f"../.venv/{sub}/{exe}", f"../venv/{sub}/{exe}"]


def find_python(comfy_dir: Path) -> Path | None:
    for rel in python_rels():
        p = comfy_dir / rel
        if p.is_file():
            return Path(os.path.abspath(p))  # abspath, not resolve(): keep the venv's own python.exe/symlink
    return None


def app_config_dirs() -> list[Path]:
    """Where the Comfy Desktop (Electron) app keeps its settings on this OS."""
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
    elif IS_MAC:
        base = HOME / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    return [base / "Comfy Desktop", base / "ComfyUI"]


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def candidate(label: str, comfy_dir: Path, python: Path | None = None, extra: list[str] | None = None) -> dict:
    return dict(label=label, dir=comfy_dir, python=python or find_python(comfy_dir), extra=extra or [])


def discover() -> list[dict]:
    found: list[dict] = []
    seen: set[Path] = set()

    def add(c: dict):
        if c["dir"] not in seen:
            seen.add(c["dir"])
            found.append(c)

    for cd in app_config_dirs():
        # Comfy Desktop (current app)
        data = read_json(cd / "installations.json")
        for inst in data if isinstance(data, list) else []:
            if not isinstance(inst, dict):
                continue
            d = resolve_comfy_dir(inst.get("installPath"))
            if not d:
                continue
            extra = []
            yaml = cd / "instance-model-paths" / f"{inst.get('id')}.yaml"
            if yaml.is_file():
                for m in re.finditer(r"(?m)^\s*base_path:\s*'?([^'\r\n]+)'?", yaml.read_text(encoding="utf-8", errors="replace")):
                    v = m.group(1).strip()
                    if Path(v).exists():
                        extra.append(v)
            add(candidate(f"Comfy Desktop ({inst.get('name', '?')})", d, extra=extra))
        # Older ComfyUI desktop app (Windows)
        old = read_json(cd / "config.json")
        if IS_WINDOWS and isinstance(old, dict) and old.get("basePath"):
            base = Path(old["basePath"])
            progs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
            code = None
            for d in sorted(progs.iterdir()) if progs.is_dir() else []:
                code = resolve_comfy_dir(d / "resources" / "ComfyUI")
                if code:
                    break
            py = base / ".venv" / "Scripts" / "python.exe"
            if code and py.is_file():
                add(candidate("ComfyUI desktop app", code, py, [str(base / "models")]))

    # Portable builds and git checkouts in common places
    names = ["ComfyUI_windows_portable", "ComfyUI", "comfyui"]
    spots = [HOME, HOME / "Desktop", HOME / "Downloads", HOME / "Documents", HOME / "dev", HOME / "src", HOME / "git"]
    if IS_WINDOWS:
        spots += [Path("C:/"), Path("D:/")]
    else:
        spots += [Path("/opt"), Path("/usr/local")]
    for spot in spots:
        for n in names:
            d = resolve_comfy_dir(spot / n)
            if d:
                add(candidate("portable" if "portable" in n else f"found at {spot / n}", d))
    return found


def choose_comfy(given: str | None) -> dict:
    if given:
        d = resolve_comfy_dir(given)
        if not d:
            fail(f"No ComfyUI found in '{given}' (looked for main.py).")
        return candidate("given path", d)
    cands = discover()
    if len(cands) == 1:
        ok(f"Found: {cands[0]['label']} - {cands[0]['dir']}")
        return cands[0]
    if len(cands) > 1:
        info("Found more than one ComfyUI:")
        for i, c in enumerate(cands, 1):
            info(f"  {i}) {c['label']} - {c['dir']}")
        n = "1" if YES else prompt("Which one? [1]", "1")
        if not n.isdigit() or not 1 <= int(n) <= len(cands):
            fail(f"Pick a number from 1 to {len(cands)}.")
        return cands[int(n) - 1]
    info("Couldn't find ComfyUI automatically.")
    info("Install it from https://www.comfy.org/download (the Desktop app is easiest on Windows)")
    info("or https://github.com/comfyanonymous/ComfyUI, or tell me where it is.")
    p = prompt("Paste the path to your ComfyUI folder (or press Enter to browse):")
    if not p:
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            p = filedialog.askdirectory(title="Select your ComfyUI folder (the one that contains main.py)")
            root.destroy()
        except Exception:
            p = ""
    d = resolve_comfy_dir(p)
    if not d:
        fail("No ComfyUI found there (looked for main.py). Re-run setup and pick the ComfyUI folder.")
    return candidate("chosen", d)


# ------------------------------------------------------------------ 2. checks

def total_ram_gb() -> int:
    try:
        if IS_WINDOWS:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30)
        if IS_MAC:
            return round(int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) / 2**30)
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20)
    except Exception:
        pass
    return 0


def check_comfy(pick: dict) -> None:
    nodes = pick["dir"] / "comfy_extras" / "nodes_flux.py"
    if not (nodes.is_file() and "FluxKontextImageScale" in nodes.read_text(encoding="utf-8", errors="replace")):
        fail("This ComfyUI is too old for FLUX Kontext. Update ComfyUI, then re-run setup.")
    ok("ComfyUI version supports FLUX Kontext")

    try:
        out = subprocess.run([str(pick["python"]), "-s", "-c", CHECK_CODE], capture_output=True, text=True, timeout=300).stdout
        r = json.loads(out.strip().splitlines()[-1])
    except Exception:
        fail("ComfyUI's Python didn't run correctly. Is ComfyUI fully installed?")
    if r.get("torch_error"):
        fail(f"PyTorch isn't working in ComfyUI's Python: {r['torch_error']}")
    if r.get("missing"):
        fail(f"ComfyUI's Python is missing: {', '.join(r['missing'])}. Update/repair ComfyUI.")

    ram = total_ram_gb()
    ram_txt = f"{ram} GB" if ram else "unknown"
    if r.get("gpu"):
        ok(f"Graphics card: {r['gpu']} ({r['vram_gb']} GB)   RAM: {ram_txt}")
        if r["vram_gb"] < 7.5:
            warn("Under 8 GB of video memory: it may still work, just slower.")
        if r.get("vendor") == "AMD":
            info("AMD support is new. If pages fail or look wrong, please report it (comfy/server.log helps).")
        if r.get("vendor") == "Intel":
            warn("Intel graphics are untested with Linecrafty. It may work; please report how it goes.")
    else:
        warn("ComfyUI didn't find a supported graphics card (NVIDIA or AMD).")
        warn("Without one, making a page would take a very long time (or fail).")
        info("Make sure ComfyUI's PyTorch matches your card (CUDA build for NVIDIA, ROCm build for AMD).")
        if not ask("Continue anyway?", "n"):
            raise Stop("")
    if 0 < ram < 15:
        warn(f"Only {ram} GB of RAM. 16 GB is the minimum and 32 GB is recommended; pages will probably fail.")
        if not ask("Continue anyway?", "n"):
            raise Stop("")
    elif 0 < ram < 30:
        info(f"{ram} GB of RAM: should work, but pages take longer. Close other programs while it runs (32 GB recommended).")


# ------------------------------------------------------------------ 3. SVG tracer

def install_vtracer(py: Path) -> None:
    lib = ROOT / "lib"
    probe = "import sys; sys.path.insert(0, sys.argv[1]); import vtracer"

    def has() -> bool:
        return subprocess.run([str(py), "-s", "-c", probe, str(lib)], capture_output=True).returncode == 0

    if not has():
        subprocess.run([str(py), "-s", "-m", "pip", "install", "--target", str(lib), "--upgrade",
                        "--disable-pip-version-check", "--no-warn-script-location", "-q", "vtracer"],
                       capture_output=True)
        if not has() and shutil.which("uv"):  # uv-made venvs often have no pip
            subprocess.run(["uv", "pip", "install", "--python", str(py), "--target", str(lib), "--upgrade", "-q", "vtracer"],
                           capture_output=True)
    if has():
        ok("vtracer ready")
    else:
        warn("Couldn't install vtracer; pages will be PNG only (no SVG).")


# ------------------------------------------------------------------ 4. models

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, part: Path, size: int) -> None:
    """Download to `part`, resuming with HTTP Range, until it has `size` bytes."""
    tries = 0
    while not (part.exists() and part.stat().st_size >= size):
        tries += 1
        if tries > 6:
            fail(f"Download of {part.name[:-5]} keeps failing. Check your internet and re-run setup (it resumes).")
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-", "User-Agent": "linecrafty-setup"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status != 206:  # server ignored the range: start over
                    have = 0
                last = 0.0
                with open(part, "ab" if have else "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 0.5:
                            print(f"\r   {have / size * 100:5.1f}%  {gb(have)} / {gb(size)}   ", end="", flush=True)
                            last = time.time()
            print(f"\r   100.0%  {gb(min(have, size))} / {gb(size)}   ")
        except urllib.error.HTTPError as e:
            if e.code == 416 and have > size:  # leftover bigger than the file: bad, start over
                part.unlink(missing_ok=True)
            warn(f"Download problem (HTTP {e.code}); retrying...")
            time.sleep(5)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            print()
            warn(f"Download interrupted ({e}); retrying...")
            time.sleep(5)


def get_models(pick: dict) -> None:
    bases = [ROOT / "models", pick["dir"] / "models"] + [Path(p) for p in pick["extra"]]
    todo = []
    for m in MODELS:
        have = next((b / m["folder"] / m["name"] for b in bases
                     if (b / m["folder"] / m["name"]).is_file() and (b / m["folder"] / m["name"]).stat().st_size == m["size"]), None)
        if have:
            ok(f"have     {m['name']}")
        else:
            info(f"need     {m['name']}  ({gb(m['size'])})")
            todo.append(m)
    if not todo:
        ok("All models present")
        return

    total = sum(m["size"] for m in todo)
    partial = sum((ROOT / "models" / m["folder"] / (m["name"] + ".part")).stat().st_size
                  for m in todo if (ROOT / "models" / m["folder"] / (m["name"] + ".part")).exists())
    needed = total - partial
    (ROOT / "models").mkdir(exist_ok=True)
    free = shutil.disk_usage(ROOT / "models").free
    info()
    info(f"Download size: {gb(needed)} into {ROOT / 'models'}  (free space there: {gb(free)})")
    if free < needed + 2**30:
        fail(f"Not enough free disk space for {ROOT / 'models'}.")
    info("Note: FLUX.1 Kontext [dev] and 4x-AnimeSharp are licensed for NON-COMMERCIAL use only")
    info("(see the License section of README.md). Pages you make are for personal use.")
    if not ask("Download now and accept those licenses? (can take a while; safe to interrupt and re-run)"):
        fail("Models not downloaded. Re-run setup when ready.")

    for m in todo:
        d = ROOT / "models" / m["folder"]
        d.mkdir(parents=True, exist_ok=True)
        dest = d / m["name"]
        part = d / (m["name"] + ".part")
        print(f"\n   Downloading {m['name']} ({gb(m['size'])})")
        download(m["url"], part, m["size"])
        print("   Verifying...", end="", flush=True)
        if sha256_of(part) != m["sha256"]:
            part.unlink(missing_ok=True)
            print()
            fail(f"{m['name']} was corrupted in download and has been deleted. Re-run setup to download it again.")
        os.replace(part, dest)
        print(" " + _c("32", "OK"))
    ok("All models present")


# ------------------------------------------------------------------ main

def run(comfy_path: str | None) -> None:
    print("\n  Linecrafty - setup\n  ---------------------------")

    step("Looking for ComfyUI")
    pick = choose_comfy(comfy_path)
    if not pick["python"]:
        info("Couldn't find the Python that ComfyUI uses (no .venv, venv or python_embeded next to it).")
        info("That's the Python where ComfyUI's requirements are installed.")
        p = Path(os.path.expanduser(prompt("Paste the full path to ComfyUI's python:")))
        if not p.is_file():
            fail(f"python not found at '{p}'.")
        pick["python"] = Path(os.path.abspath(p))
    ok(f"ComfyUI code: {pick['dir']}")
    ok(f"Its Python:   {pick['python']}")

    step("Checking ComfyUI, graphics card and memory")
    check_comfy(pick)

    step("Installing the SVG tracer (small, into this folder only)")
    install_vtracer(pick["python"])

    step("Local AI models (they run on this PC; nothing is sent anywhere)")
    get_models(pick)

    step("Saving settings")
    cfg = {"comfy_dir": str(pick["dir"]), "python": str(pick["python"]), "extra_model_dirs": pick["extra"]}
    (ROOT / "config.json").write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    for d in ("input", "output"):
        (ROOT / d).mkdir(exist_ok=True)
    ok("Done")

    launcher = Path("launchers") / "make_coloring_page.py"
    print("\n" + _c("32", "  All set!"))
    print(f"  Run:  python {launcher} photo.jpg      (or put pictures in 'input' and run it with no pictures)")
    if IS_WINDOWS:
        print(f"  Or drag pictures onto '{launcher}' in File Explorer.")
    print("  Each page takes about 2-4 minutes. Finished pages appear in the 'output' folder.\n")


def main() -> int:
    global YES
    ap = argparse.ArgumentParser(description="Set up linecrafty.")
    ap.add_argument("comfy", nargs="?", help="your ComfyUI folder (the one containing main.py, or its parent)")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask before downloading")
    ap.add_argument("--no-pause", action="store_true", help="don't wait for Enter at the end (Windows)")
    a = ap.parse_args()
    YES = a.yes
    code = 0
    try:
        run(a.comfy)
    except Stop as e:
        if str(e):
            print("\n" + _c("31", f"SETUP STOPPED: {e}"))
        code = 1
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run setup to continue (downloads resume).")
        code = 130
    if IS_WINDOWS and not a.no_pause and not a.yes:  # keep a double-clicked window open
        try:
            input("Press Enter to close...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
