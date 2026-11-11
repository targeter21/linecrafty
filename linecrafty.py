r"""linecrafty - turn color pictures into coloring-book pages, locally.

Pipeline:
  1. FLUX.1 Kontext dev (via a headless ComfyUI) redraws the picture as line art.
  2. 4x-AnimeSharp upscales it 4x.
  3. Cleanup: grey -> pure black/white, specks removed  -> <name>_lineart.png
  4. vtracer traces it to vectors                        -> <name>_lineart.svg

Usage:
  linecrafty                                convert everything in input\ (then moved to input\done)
  linecrafty photo.jpg [more.jpg ...]       convert files
  linecrafty some\folder                    convert every image in a folder
  linecrafty photo.jpg --variants 3         3 different takes (different seeds)
  linecrafty photo.jpg --style all          full + simple + no-background versions
  linecrafty --stop                         stop a server left running with --keep-server

Runs with ComfyUI's own Python. setup.cmd writes config.json (where ComfyUI is) and installs
vtracer into lib\ - nothing is installed into ComfyUI itself.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "lib"))  # vtracer lives here (pip --target), keeps ComfyUI's env untouched

import numpy as np  # noqa: E402  (from ComfyUI's Python)
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402

WORK = ROOT / "comfy"
CONFIG = ROOT / "config.json"
HOST, PORT = "127.0.0.1", 8288
URL = f"http://{HOST}:{PORT}"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MODEL_FOLDERS = ("diffusion_models", "text_encoders", "upscale_models", "vae", "loras")

BASE_PROMPT = (
    "Convert this image into a black and white coloring book page for kids. "
    "Clean, smooth, confident black ink outlines on pure white paper. "
    "No color, no grey, no shading, no gradients, no solid black fills, no hatching. "
    "Keep the same characters, faces, expressions, pose, clothing and composition, "
    "drawn as closed shapes that are easy to color in. "
)
STYLES = {
    # full line art (default) - the model tends to drop backgrounds unless told explicitly
    "full": "Redraw the entire background scenery as line art too: every building, tree, plant, rock, "
            "animal, moon and bird, all the way to the edges of the picture.",
    "simple": "Make it a very simple coloring page for toddlers: only a few thick, bold marker outlines, "
              "big open areas to color, no hair strands, no fabric patterns, no fur, no texture lines, "
              "no tiny details. Keep a few simple background shapes.",
    "nobg": "Remove the background completely: draw only the main characters, "
            "isolated on plain white paper with nothing behind them.",
}
# Prompting alone barely simplifies, so "simple" is also generated at low resolution:
# with few pixels the model can only draw a handful of bold strokes; the 4x upscaler sharpens them.
STYLE_MEGAPIXELS = {"simple": 0.3}


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit("Not set up yet: run setup.cmd first.")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for key in ("comfy_dir", "python"):
        if not Path(cfg.get(key, "")).exists():
            sys.exit(f"config.json: {key} = {cfg.get(key)!r} not found. Re-run setup.cmd.")
    return cfg


# --------------------------------------------------------------------------- server

def _get(path: str, timeout: float = 5):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def server_up() -> bool:
    try:
        _get("/system_stats", timeout=2)
        return True
    except Exception:
        return False


def write_model_paths(cfg: dict) -> Path:
    """ComfyUI already searches its own models folder; add ours plus any extra (e.g. Comfy Desktop shared)."""
    bases = [ROOT / "models"] + [Path(p) for p in cfg.get("extra_model_dirs", [])]
    lines = []
    for i, base in enumerate(bases):
        lines.append(f"linecrafty_{i}:")
        lines.append(f"  base_path: '{base.as_posix()}'")
        lines += [f"  {f}: {f}/" for f in MODEL_FOLDERS]
    path = WORK / "extra_model_paths.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def start_server(cfg: dict) -> subprocess.Popen | None:
    if server_up():
        return None
    for d in ("input", "output", "temp", "user"):
        (WORK / d).mkdir(parents=True, exist_ok=True)
    log = open(WORK / "server.log", "w", encoding="utf-8")
    cmd = [
        cfg["python"], "-s", "main.py",
        "--listen", HOST, "--port", str(PORT),
        "--extra-model-paths-config", str(write_model_paths(cfg)),
        "--input-directory", str(WORK / "input"),
        "--output-directory", str(WORK / "output"),
        "--temp-directory", str(WORK / "temp"),
        "--user-directory", str(WORK / "user"),
        "--disable-all-custom-nodes", "--disable-auto-launch",
    ]
    print("Starting the drawing engine (first start takes ~30 s; log: comfy\\server.log)...", flush=True)
    proc = subprocess.Popen(cmd, cwd=cfg["comfy_dir"], stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    (WORK / "server.pid").write_text(str(proc.pid))
    for _ in range(300):
        if proc.poll() is not None:
            sys.exit(f"ComfyUI exited during startup; see {WORK / 'server.log'}")
        if server_up():
            return proc
        time.sleep(1)
    sys.exit("ComfyUI did not come up within 5 minutes; see comfy\\server.log")


def stop_server(proc: subprocess.Popen | None = None) -> None:
    pid_file = WORK / "server.pid"
    pid = proc.pid if proc else (int(pid_file.read_text()) if pid_file.exists() else None)
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    pid_file.unlink(missing_ok=True)


# --------------------------------------------------------------------------- workflow

def build_workflow(image_name: str, prompt: str, seed: int, steps: int, guidance: float,
                   prefix: str, megapixels: float | None = None) -> dict:
    if megapixels:
        scale = {"class_type": "ImageScaleToTotalPixels",
                 "inputs": {"image": ["load", 0], "upscale_method": "area",
                            "megapixels": megapixels, "resolution_steps": 16}}
    else:  # Kontext's preferred ~1MP size for this aspect ratio
        scale = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["load", 0]}}
    return {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": "flux1-dev-kontext_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "clip": {"class_type": "DualCLIPLoader",
                 "inputs": {"clip_name1": "clip_l.safetensors",
                            "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": scale,
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["text", 0], "latent": ["enc", 0]}},
        "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["ref", 0], "guidance": guidance}},
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "sample": {"class_type": "KSampler",
                   "inputs": {"model": ["unet", 0], "positive": ["guid", 0], "negative": ["neg", 0],
                              "latent_image": ["enc", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                              "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save_raw": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix + "_raw"}},
        "upm": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-AnimeSharp.pth"}},
        "up": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["upm", 0], "image": ["dec", 0]}},
        "save_up": {"class_type": "SaveImage", "inputs": {"images": ["up", 0], "filename_prefix": prefix + "_up"}},
    }


def run_workflow(wf: dict) -> dict[str, Path]:
    body = json.dumps({"prompt": wf, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(URL + "/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pid = json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"ComfyUI rejected the workflow:\n{e.read().decode(errors='replace')}")
    while True:
        time.sleep(1.5)
        hist = _get(f"/history/{pid}")
        if pid not in hist:
            continue
        entry = hist[pid]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
            sys.exit(f"Generation failed: {json.dumps(msgs, indent=1)[:2000]}")
        if not status.get("completed"):
            continue
        out = {}
        for node_id, o in entry["outputs"].items():
            for img in o.get("images", []):
                out[node_id] = WORK / "output" / img.get("subfolder", "") / img["filename"]
        return out


# --------------------------------------------------------------------------- cleanup

def clean_lineart(img_path: Path, black: int, white: int, speck: int) -> np.ndarray:
    g = np.asarray(Image.open(img_path).convert("L"), dtype=np.float32)
    # Levels: darker than `black` -> ink, lighter than `white` -> paper, smooth ramp between
    # (keeps anti-aliased edges instead of jaggies).
    out = (np.clip((g - black) / max(1, white - black), 0, 1) * 255).astype(np.uint8)
    # Remove tiny isolated specks of ink.
    labels, _ = ndimage.label(out < 128, structure=np.ones((3, 3)))
    small = np.bincount(labels.ravel()) < speck
    small[0] = False
    out[small[labels]] = 255
    return out


def to_svg(png_path: Path, svg_path: Path) -> bool:
    try:
        import vtracer
    except ImportError:
        return False
    vtracer.convert_image_to_svg_py(
        str(png_path), str(svg_path), colormode="binary", mode="spline",
        filter_speckle=8, corner_threshold=60, length_threshold=4.0,
        splice_threshold=45, path_precision=2,
    )
    return True


# --------------------------------------------------------------------------- main

def collect(inputs: list[str]) -> list[Path]:
    files = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"skip: {s} not found")
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description="Turn color pictures into coloring-book pages.")
    ap.add_argument("inputs", nargs="*", help="image files or folders (default: the input folder)")
    ap.add_argument("-o", "--out", default=str(ROOT / "output"), help="output folder (default: output\\)")
    ap.add_argument("-s", "--style", default="full",
                    help="full (default), simple, nobg, a comma list like full,nobg, or 'all'")
    ap.add_argument("--variants", type=int, default=1, help="how many takes per image (different seeds)")
    ap.add_argument("--seed", type=int, default=None, help="fixed seed (default: random)")
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--guidance", type=float, default=2.5)
    ap.add_argument("--prompt", default=None, help="replace the whole instruction prompt (ignores --style)")
    ap.add_argument("--extra", default="", help="text appended to the prompt, e.g. 'leave out the dog'")
    ap.add_argument("--black", type=int, default=90, help="cleanup: grey at or below this becomes ink")
    ap.add_argument("--white", type=int, default=200, help="cleanup: grey at or above this becomes paper")
    ap.add_argument("--speck", type=int, default=60, help="cleanup: remove ink blobs smaller than N pixels")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the output folder when done")
    ap.add_argument("--keep-server", action="store_true", help="leave the engine running for faster next run")
    ap.add_argument("--stop", action="store_true", help="stop a server left running and exit")
    a = ap.parse_args()

    if a.stop:
        stop_server()
        print("stopped")
        return
    cfg = load_config()

    inbox = ROOT / "input"
    from_inbox = not a.inputs
    files = collect(a.inputs or [str(inbox)])
    if not files:
        print(f"No pictures to convert. Put some in {inbox} or drag them onto 'Make Coloring Page.cmd'.")
        return

    styles = list(STYLES) if a.style == "all" else [s.strip() for s in a.style.split(",") if s.strip()]
    bad = [s for s in styles if s not in STYLES]
    if bad:
        sys.exit(f"unknown style {bad}; choose from {list(STYLES)} or 'all'")
    if a.prompt:
        styles = ["custom"]

    jobs = []  # (file, stem, prompt, seed, megapixels)
    for f in files:
        for style in styles:
            prompt = a.prompt if style == "custom" else BASE_PROMPT + STYLES[style]
            if a.extra:
                prompt += " " + a.extra
            for v in range(a.variants):
                seed = a.seed + v if a.seed is not None else random.randrange(2**32)
                stem = f.stem + ("" if style in ("full", "custom") else f"_{style}")
                stem += "" if a.variants == 1 else f"_v{v + 1}"
                jobs.append((f, stem, prompt, seed, STYLE_MEGAPIXELS.get(style)))

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = start_server(cfg)
    svg_warned = False
    try:
        for n, (f, stem, prompt, seed, mp) in enumerate(jobs, 1):
            in_name = f"{uuid.uuid4().hex[:8]}{f.suffix.lower()}"
            shutil.copy(f, WORK / "input" / in_name)
            t0 = time.time()
            print(f"[{n}/{len(jobs)}] {f.name} -> {stem}  (seed {seed}) ...", end="", flush=True)
            res = run_workflow(build_workflow(in_name, prompt, seed, a.steps, a.guidance, stem, mp))
            shutil.copy(res["save_raw"], out_dir / f"{stem}_raw.png")
            clean = clean_lineart(res["save_up"], a.black, a.white, a.speck)
            png = out_dir / f"{stem}_lineart.png"
            Image.fromarray(clean).save(png, dpi=(600, 600))
            if not a.no_svg and not to_svg(png, out_dir / f"{stem}_lineart.svg") and not svg_warned:
                print(" (SVG skipped: vtracer not installed; re-run setup.cmd)", end="")
                svg_warned = True
            for p in res.values():
                p.unlink(missing_ok=True)
            (WORK / "input" / in_name).unlink(missing_ok=True)
            print(f" done in {time.time() - t0:.0f}s")
        if from_inbox:  # don't redo them next time
            done = inbox / "done"
            done.mkdir(exist_ok=True)
            for f in files:
                shutil.move(str(f), str(done / f.name))
    finally:
        if proc and not a.keep_server:
            stop_server(proc)
    print(f"Finished. Pages are in: {out_dir}")
    if a.open:
        os.startfile(out_dir)


if __name__ == "__main__":
    main()
