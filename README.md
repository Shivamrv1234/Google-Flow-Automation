# Flow Bulk Image Generator

Bulk-generate images in **Google Flow** from a text file of prompts, then download every image in **2K**, named by its prompt number (`001.png`, `002.png`, …).

> Unofficial helper for your own Google account. Not affiliated with Google. Flow's page can change at any time; when a step fails, the script saves a `flow_error_*.png` screenshot and stops.

---

## What it does

1. **Generate:** it sends your prompts to Flow's agent chat in batches (20 per message by default). The shared style is written once per message, and the agent is asked to name each image `Scene 004`, `Scene 005`, … by prompt number. The script waits until the page goes quiet, then sends the next batch.
2. **Download:** it scrolls through your project, reads each image's `Scene ###` name (or matches the prompt text Flow shows for it), opens **⋮ → Download → 2K Upscaled**, and saves the file as `004.png`. Images it can't identify are saved as `unmatched_###.png`.
3. **Resume:** progress is saved in `flow_progress.json`, so if anything stops you can run the same command again and it picks up where it left off.

---

## Requirements

- Python 3.8+
- Google Chrome
- A Google account with access to [Google Flow](https://labs.google/flow)

---

## Installation

```bash
git clone https://github.com/<your-username>/flow-bulk-image-generator.git
cd flow-bulk-image-generator
pip install -r requirements.txt
```

Or install the one dependency directly:

```bash
pip install playwright
```

> The script attaches to *your own* Chrome, so you don't need `playwright install`.

---

## Step 1: Write your prompts

Put your prompts in `prompts.txt`, **one per line, in order**. Lines may be numbered or not:

```text
1. A wide shot of a lighthouse on a cliff at dawn. Style: cinematic, 35mm film, muted colours
2. A fisherman mending nets on the dock. Style: cinematic, 35mm film, muted colours
A seagull landing on a wooden post at sunset. Style: cinematic, 35mm film, muted colours
```

- Numbered lines (`12. …` or `12) …`) keep their number.
- Unnumbered lines continue counting from the previous number.
- Anything after `Style:` is treated as style. The most common style in a batch is written once for the whole batch; prompts with a different style keep their own.

---

## Step 2: Start Chrome with remote debugging

Close **all** Chrome windows first, then start Chrome with a debugging port. Recent Chrome versions require a separate profile folder (`--user-data-dir`) for this to work.

**Windows (Command Prompt):**
```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeFlowProfile"
```

**Windows (PowerShell):**
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeFlowProfile"
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="$HOME/ChromeFlowProfile"
```

**Linux:**
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/ChromeFlowProfile"
```

To check that it worked, open `http://localhost:9222/json/version` in that Chrome window. You should see some JSON.

Then, in that same Chrome window:
1. Sign in to your Google account (only the first time, because the profile folder remembers it).
2. Open your Flow project.
3. Make sure the **agent chat panel** is visible.

---

## Step 3: Run it

### Generate and download everything (one command)

```bash
python flow_bulk.py all --prompts prompts.txt --downloads "C:\FlowImages"
```

macOS / Linux:
```bash
python3 flow_bulk.py all --prompts prompts.txt --downloads ~/FlowImages
```

### Generate only

```bash
python flow_bulk.py generate --prompts prompts.txt
python flow_bulk.py generate --prompts prompts.txt --batch 20
```

### Download only

```bash
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages"
```

### First download: the one-time "teach" step

The first time you download, the script asks you to show it the menu once:

1. Hover over any image and click its **three-dot menu (⋮)**.
2. Hover over **Download** (the 1K / 2K / 4K list opens).
3. Click **2K Upscaled**.

It records those clicks in `flow_teach.json` and then downloads the rest by itself. To redo this (for example after Flow changes its layout):

```bash
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages" --teach
```

---

## More commands

### Test the download with just 2 images
```bash
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages" --limit 2
```

### Generate only some prompts
```bash
python flow_bulk.py generate --prompts prompts.txt --only 5,9,40-60
```

### Regenerate prompts that were already sent
```bash
python flow_bulk.py generate --prompts prompts.txt --only 12,15 --regenerate
```

### Regenerate everything that has no image yet
After a download run, the script writes the missing numbers to `missing.txt`:
```bash
python flow_bulk.py generate --prompts prompts.txt --only missing.txt --regenerate
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages"
```

### Start generation over from scratch
```bash
python flow_bulk.py generate --prompts prompts.txt --reset
```

### Retry failed downloads
Run the same download command again. Files that already exist are skipped:
```bash
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages"
```

### Download again even if the file already exists
```bash
python flow_bulk.py download --prompts prompts.txt --downloads "C:\FlowImages" --no-skip-existing
```

### Slower, more patient batches (for big or slow batches)
```bash
python flow_bulk.py generate --prompts prompts.txt --batch 15 --min-wait 120 --quiet 90 --batch-timeout 3600
```

### Use a different Chrome debugging port
```bash
python flow_bulk.py all --prompts prompts.txt --downloads "C:\FlowImages" --port 9333
```
(Start Chrome with `--remote-debugging-port=9333` to match.)

### Show help
```bash
python flow_bulk.py --help
```

---

## All options

| Option | Used by | Default | What it does |
|---|---|---|---|
| `generate` / `download` / `all` | — | *(required)* | Which step to run |
| `--prompts FILE` | all | *(required)* | Text file with your prompts, one per line |
| `--downloads FOLDER` | download, all | — | Folder to save the 2K images in (required for `download` and `all`) |
| `--batch N` | generate | `20` | Prompts per message (Flow handles about 20–25) |
| `--min-wait SEC` | generate | `60` | Seconds to wait before checking whether a batch is done |
| `--quiet SEC` | generate | `45` | Seconds of no change on the page that mean a batch is finished |
| `--batch-timeout SEC` | generate | `1800` | Maximum seconds to wait for one batch before sending the next |
| `--reset` | generate | off | Forget which prompts were already sent |
| `--only LIST` | generate | all | Numbers to do, e.g. `5,9,40-60`, or a file such as `missing.txt` |
| `--regenerate` | generate | off | Send prompts again even if they were sent before |
| `--limit N` | download | `0` (no limit) | Download only N images (use `2` for a test) |
| `--wait SEC` | download | `120` | Seconds to wait for each 2K file to arrive |
| `--min-score 0-1` | download | `0.5` | How sure a prompt-text match must be to name a file |
| `--teach` | download | off | Redo the one-time teach step for the download menu |
| `--pause SEC` | download | `1.5` | Seconds between downloads |
| `--no-open` | download | off | Never open an image to read its prompt (only use `Scene ###` labels) |
| `--no-skip-existing` | download | off | Download again even if `NNN.png` already exists |
| `--port N` | all | `9222` | Chrome remote-debugging port |

---

## Files the script creates

| File | Purpose |
|---|---|
| `flow_progress.json` | Which prompts were sent and which images were downloaded (for resuming) |
| `flow_teach.json` | The recorded ⋮ → Download → 2K clicks from the teach step |
| `missing.txt` | Prompt numbers that have no downloaded image yet |
| `flow_error_*.png` | Screenshot taken when a step fails |
| `NNN.png` | Your images, named by prompt number |
| `unmatched_NNN.png` | Images the script couldn't match to a prompt (check these by hand) |
| `NNN_v2.png` | A second image for the same prompt number |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not attach to Chrome` | Close every Chrome window and start Chrome again with the Step 2 command. Check `http://localhost:9222/json/version`. |
| `No Google Flow tab found` | Open your Flow project in the Chrome window started with the debugging port. |
| `could not find Flow's prompt box` | Open the agent chat panel in Flow so the prompt box is visible. |
| `the message stayed in the prompt box` | Flow's send button may have changed. Edit `SEND_BUTTON` at the top of the script. |
| `three-dot menu not found` / `2K Upscaled not found` | Run the download again with `--teach`. |
| Many `unmatched_` files | Make sure the agent named images `Scene ###`. Try lowering `--min-score` (e.g. `0.4`). |
| Batch never finishes | Increase `--batch-timeout`, or lower `--batch`. |
| Some scenes are missing | Use the "Regenerate everything that has no image yet" commands above. |

If Flow renames buttons or menus, edit the patterns in the **settings** section at the top of `flow_bulk.py` (`PROMPT_BOX_HINT`, `SEND_BUTTON`, `STOP_BUTTON`, `SCENE_LABEL`, `DOWNLOAD_ITEM`, `TWO_K_ITEM`).

---

## Disclaimer

This is an unofficial automation tool for use with your own account. Use it in line with Google's Terms of Service and Flow's usage limits. The authors are not responsible for any account restrictions or changes to Flow that break the script.

## License

MIT
