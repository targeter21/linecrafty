# Linecrafty

Turn any color picture into a clean black-and-white **coloring-book page** with local AI that runs
entirely on your own Windows PC. Nothing is uploaded anywhere, and no account or cloud service is
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

- Windows 10 or 11
- An **NVIDIA or AMD** graphics card with 8 GB or more of video memory
  - NVIDIA: RTX 3060 / 3070 / 4060 or better. Tested.
  - AMD: Radeon RX 7700 XT / 7800 XT / 7900 or better, using ComfyUI's AMD (ROCm) version. Being
    tested; please report how it goes.
- System memory (RAM):
  - **32 GB recommended**
  - **16 GB minimum**: should work, but pages take longer; close other programs while it runs.
    Not yet tested; we'd like to hear how it goes.
- About 18 GB of free disk space for the local AI models
- **ComfyUI**. The easiest is the [Comfy Desktop app](https://www.comfy.org/download): install it and
  open it once so it finishes setting itself up. You can close it afterwards.

## Setup (once)

1. Get Linecrafty: the green **Code** button above > **Download ZIP**, then unzip it somewhere with
   ~18 GB free (e.g. `C:\Linecrafty`). Or `git clone https://github.com/wisteso/linecrafty`.
2. Double-click **`setup.cmd`**.

Setup finds your ComfyUI (Comfy Desktop, the older ComfyUI desktop app, or a portable install; if it
can't, it asks you to pick the folder), checks your graphics card and memory, and downloads the local
AI models (~17.7 GB).
It asks before downloading, verifies every file against its SHA-256 hash, and resumes if interrupted:
just run `setup.cmd` again. It never changes your ComfyUI install; the only thing it installs is the
small `vtracer` library, into this folder's `lib\`.

If Windows warns about running the files, right-click the zip > Properties > tick **Unblock**
before unzipping.

## Making pages

Drag one or more pictures onto one of these:

- **`Make Coloring Page.cmd`**: full line art, the usual choice
- **`Make Simple Coloring Page.cmd`**: fewer, bolder lines
- **`Make Coloring Page (no background).cmd`**: just the characters

Or put pictures in the `input` folder and double-click one of them. It converts them all and moves
them to `input\done`.

A page takes about 2-4 minutes (simple: about 1 minute) on an 8 GB card. When it's done, the
`output` folder opens. For each picture you get:

| File | |
|---|---|
| `name_lineart.png` | The coloring page, 3328x4992. Print this one. |
| `name_lineart.svg` | Vector version, sharp at any size |
| `name_raw.png` | The local AI's direct output, before cleanup |

Every run draws a slightly different version, so if you don't like one, run it again.

## Command line

`linecrafty.cmd` is the underlying tool (`linecrafty.cmd --help` for everything):

```bat
linecrafty.cmd photo.jpg                       :: full style -> output\
linecrafty.cmd photo.jpg --style all           :: full + simple + nobg
linecrafty.cmd photo.jpg --variants 3          :: three different takes
linecrafty.cmd photo.jpg --extra "Leave out the dog."
linecrafty.cmd C:\pics -o C:\pages             :: a whole folder
linecrafty.cmd photo.jpg --keep-server         :: keep models loaded; later runs start faster
linecrafty.cmd --stop                          :: unload them
```

Cleanup knobs: `--black 90 --white 200` (levels), `--speck 60` (smallest ink blob kept, in pixels).
Generation knobs: `--steps 24 --guidance 2.5 --seed N`.

## How it works

1. `linecrafty.py` starts its own hidden ComfyUI (using your install's code and Python) on port 8288,
   with custom nodes disabled and its own input/output/user folders under `comfy\`. Your ComfyUI's
   settings and workflows are untouched.
2. FLUX.1 Kontext dev (fp8) gets the picture plus an instruction prompt (see `STYLES` in
   `linecrafty.py`). "simple" also runs at ~0.3 MP, because the model ignores "use fewer lines" but
   can't fit fine detail into fewer pixels.
3. 4x-AnimeSharp upscales the result 4x.
4. Levels push grey to pure black or white, small specks are removed, and vtracer traces the SVG.

`config.json` (written by setup) records which ComfyUI to use. Re-run `setup.cmd` if you move or
reinstall ComfyUI, or point it somewhere specific: `setup.cmd "D:\ComfyUI_windows_portable"`.

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
with friends, but don't sell them. By running `setup.cmd` you're downloading those models and
agreeing to their licenses.

Linecrafty runs your separately installed [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
(GPL-3.0) as its own program and talks to it over its local API. It doesn't include or modify any
ComfyUI code. It also uses NumPy, SciPy and Pillow from ComfyUI's Python, and installs
[vtracer](https://github.com/visioncortex/vtracer) (MIT).

## Troubleshooting

- **"Not set up yet"**: run `setup.cmd`.
- **"ComfyUI exited during startup"**: see `comfy\server.log`. ComfyUI usually needs updating; update
  it, then re-run `setup.cmd`.
- **Very slow or out of memory**: close other big programs (games, browsers with many tabs).
- **Moved or reinstalled ComfyUI**: re-run `setup.cmd`.
