# Linecrafty

Turn any color picture into a clean black-and-white **coloring-book page** with local AI that runs
entirely on your own Windows or Linux PC. Nothing is uploaded anywhere, and no account or cloud service is
involved. After setup, it doesn't even need the internet.

It isn't an edge-detection filter. A local AI image-editing model (**FLUX.1 Kontext [dev]**, run on
your own graphics card through [ComfyUI](https://www.comfy.org)) redraws the picture as line art the way a coloring-book artist would:
closed shapes, smooth outlines, no shading. The result is then upscaled 4x, cleaned to pure black on
white, and traced to a vector SVG so it prints sharp at any size.

Three styles:

| Style | What you get |
|---|---|
| **full** (default) | Characters plus the whole background, redrawn as line art |
| **simple** | Fewer, bolder lines and big open areas, for little kids |
| **nobg** | Just the characters on white, no scenery |

## What you need

- Windows 10/11 or Linux, with **Python 3.8 or newer** to run setup (get it from
  [python.org](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH")
- An **NVIDIA or AMD** graphics card with 8 GB or more of video memory
  - NVIDIA: RTX 3060 / 3070 / 4060 or better. Tested.
  - AMD: Radeon RX 7700 XT / 7800 XT / 7900 or better, using ComfyUI's AMD (ROCm) version. Being
    tested; please report how it goes. On Linux, use a PyTorch build that matches your card
    (CUDA for NVIDIA, ROCm for AMD).
- System memory (RAM):
  - **32 GB recommended**
  - **16 GB minimum**: should work, but pages take longer; close other programs while it runs.
    Not yet tested; we'd like to hear how it goes.
- About 18 GB of free disk space for the local AI models
- **ComfyUI**. On Windows the easiest is the [Comfy Desktop app](https://www.comfy.org/download):
  install it and open it once so it finishes setting itself up. You can close it afterwards.
  On Linux, a normal [ComfyUI](https://github.com/comfyanonymous/ComfyUI) checkout with its Python
  environment (e.g. `~/ComfyUI/venv`) works.

## Setup (once)

1. Get Linecrafty: the green **Code** button above > **Download ZIP**, then unzip it somewhere with
   ~18 GB free (e.g. `C:\Linecrafty` or `~/linecrafty`). Or `git clone https://github.com/wisteso/linecrafty`.
2. In a terminal (Command Prompt or PowerShell on Windows) in that folder, run:

   ```
   python setup.py
   ```

   (On Windows you can also double-click `setup.py`. On Linux use `python3`.) To point it at a
   specific ComfyUI install: `python setup.py /path/to/ComfyUI`; add `-y` to skip the download
   confirmation.

Setup finds your ComfyUI (Comfy Desktop, the older ComfyUI desktop app, a portable install, or on
Linux a checkout in a common place like `~/ComfyUI`; if it can't, it asks you for the folder), checks your graphics card and memory, and downloads the local
AI models (~17.7 GB).
It asks before downloading, verifies every file against its SHA-256 hash, and resumes if interrupted:
just run setup again. It never changes your ComfyUI install; the only thing it installs is the
small `vtracer` library, into this folder's `lib/`.

## Making pages

Run one of the scripts in `launchers/` with your pictures as arguments, or on Windows drag the
pictures onto the script in File Explorer:

- **`launchers/make_coloring_page.py`**: full line art, the usual choice
- **`launchers/make_simple_coloring_page.py`**: fewer, bolder lines
- **`launchers/make_coloring_page_nobg.py`**: just the characters

```
python launchers/make_coloring_page.py photo.jpg other.png
```

With no pictures given they convert everything in the `input` folder and move the originals to
`input/done`.

A page takes about 2-4 minutes (simple: about 1 minute) on an 8 GB card. When it's done, the
`output` folder opens. For each picture you get:

| File | |
|---|---|
| `name_lineart.png` | The coloring page, 3328x4992. Print this one. |
| `name_lineart.svg` | Vector version, sharp at any size |
| `name_raw.png` | The local AI's direct output, before cleanup |

Every run draws a slightly different version, so if you don't like one, run it again.

## Command line

`linecrafty.py` is the underlying tool (`python linecrafty.py --help` for everything). It works with
any Python: it re-launches itself under ComfyUI's Python, which setup remembered.

```
python linecrafty.py photo.jpg                       # full style -> output/
python linecrafty.py photo.jpg --style all           # full + simple + nobg
python linecrafty.py photo.jpg --variants 3          # three different takes
python linecrafty.py photo.jpg --extra "Leave out the dog."
python linecrafty.py some/folder -o some/pages       # a whole folder
python linecrafty.py photo.jpg --keep-server         # keep models loaded; later runs start faster
python linecrafty.py --stop                          # unload them
```

Cleanup knobs: `--black 90 --white 200` (levels), `--speck 60` (smallest ink blob kept, in pixels).
Generation knobs: `--steps 24 --guidance 2.5 --seed N`.

## Repo layout

```
linecrafty.py     the tool itself
setup.py          one-time setup (finds ComfyUI, downloads models, installs the SVG tracer)
launchers/        make_coloring_page.py and its simple / no-background variants
input/ output/    your pictures and the finished pages (created by setup)
models/ lib/      downloaded AI models and the SVG tracer (created by setup)
config.json       where your ComfyUI is (written by setup)
```

Everything is plain Python using only the standard library (plus numpy, Pillow and SciPy from
ComfyUI's environment), so the same files run on Windows and Linux.

## How it works

1. `linecrafty.py` starts its own hidden ComfyUI (using your install's code and Python) on port 8288,
   with custom nodes disabled and its own input/output/user folders under `comfy/`. Your ComfyUI's
   settings and workflows are untouched.
2. FLUX.1 Kontext dev (fp8) gets the picture plus an instruction prompt (see `STYLES` in
   `linecrafty.py`). "simple" also runs at ~0.3 MP, because the model ignores "use fewer lines" but
   can't fit fine detail into fewer pixels.
3. 4x-AnimeSharp upscales the result 4x.
4. Levels push grey to pure black or white, small specks are removed, and vtracer traces the SVG.

`config.json` (written by setup) records which ComfyUI to use. Re-run setup if you move or
reinstall ComfyUI, or point it somewhere specific: `python setup.py "D:\ComfyUI_windows_portable"` or `python setup.py ~/ComfyUI`.

## Models

Downloaded by setup from Hugging Face. They aren't included in this repository.

| File | Size | Source | License |
|---|---|---|---|
| `flux1-dev-kontext_fp8_scaled.safetensors` | 11.9 GB | [Comfy-Org/Flux-1-Kontext-Dev](https://huggingface.co/Comfy-Org/Flux-1-Kontext-Dev) (Black Forest Labs) | [FLUX.1 [dev] Non-Commercial](https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev/blob/main/LICENSE.md) |
| `t5xxl_fp8_e4m3fn_scaled.safetensors` | 5.2 GB | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | Apache-2.0 |
| `clip_l.safetensors` | 0.2 GB | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | Apache-2.0 |
| `ae.safetensors` (FLUX VAE) | 0.3 GB | [Comfy-Org/Lumina_Image_2.0_Repackaged](https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged) | Apache-2.0 |
| `4x-AnimeSharp.pth` | 0.1 GB | [Kim2091/AnimeSharp](https://huggingface.co/Kim2091/AnimeSharp) by Kim2091 | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |

If a model is already in your ComfyUI models folder (or Comfy Desktop's shared models folder), setup
uses it instead of downloading it again.

## License

Linecrafty's own code (the scripts in this repository) is [MIT licensed](LICENSE).

The local AI models are **not** part of this repository and are **not** covered by that license. Setup
downloads them from their original sources, and each has its own terms (table above). Two of them,
**FLUX.1 Kontext [dev]** and **4x-AnimeSharp**, are licensed for **non-commercial use only**. So the
coloring pages Linecrafty makes with them are for personal use: print them, color them, share them
with friends, but don't sell them. By running `setup.py` you're downloading those models and
agreeing to their licenses.

Linecrafty runs your separately installed [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
(GPL-3.0) as its own program and talks to it over its local API. It doesn't include or modify any
ComfyUI code. It also uses NumPy, SciPy and Pillow from ComfyUI's Python, and installs
[vtracer](https://github.com/visioncortex/vtracer) (MIT).

## Troubleshooting

- **"Not set up yet"**: run `python setup.py`.
- **"ComfyUI exited during startup"**: see `comfy/server.log`. ComfyUI usually needs updating; update
  it, then re-run setup.
- **Very slow or out of memory**: close other big programs (games, browsers with many tabs).
- **Moved or reinstalled ComfyUI**: re-run setup.
- **Linux: the output folder doesn't open**: it needs `xdg-open`; the pages are still in `output/`.
