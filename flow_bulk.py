"""
flow_bulk.py - bulk-generate images in Google Flow from a text file of
prompts, then download every image in 2K, named by its prompt number.

  1. Paste your prompts into prompts.txt, one per line, in sequence.
     Lines may start with a number ("12. A wide shot of...") or not.
  2. Start Chrome with remote debugging (see the guide), log in to Google,
     and open your Flow project with the agent chat panel visible.
  3. Run:
        python flow_bulk.py all --prompts prompts.txt --downloads "C:\\FlowImages"

Steps you can also run on their own:
        python flow_bulk.py generate --prompts prompts.txt --batch 20
        python flow_bulk.py download --prompts prompts.txt --downloads "C:\\FlowImages"

How it generates: it sends 20 prompts at a time in ONE message to Flow's
agent, with the shared style written once, and asks the agent to name each
image "Scene 004", "Scene 005"... by its prompt number. When the agent has
finished and the page has gone quiet, it sends the next 20.

How it names files: in the download step it reads each image's
"Scene ###" name and the prompt Flow shows for it, and saves the 2K file as
004.png. If neither is clear the file is saved as unmatched_###.png.

Progress is saved in flow_progress.json, so if it stops you can run the
same command again and it carries on where it left off.

Unofficial helper for your own account. Flow's page can change; when a
step fails the script saves flow_error_*.png and stops.
"""
import argparse, json, re, sys, time
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------ settings
# Text the script looks for on Flow's page. Edit these if Flow renames things.
PROMPT_BOX_HINT = re.compile(r"create|prompt|describe|imagine|what do you want", re.I)
SEND_BUTTON = re.compile(r"send|create|generate|submit|arrow_forward|arrow_upward", re.I)
STOP_BUTTON = re.compile(r"\bstop\b|cancel generation", re.I)
SCENE_LABEL = re.compile(r"\bScene\s*0*(\d{1,4})\b", re.I)
MENU_BUTTON = re.compile(r"more|options|more_vert|menu", re.I)
DOWNLOAD_ITEM = re.compile(r"^\s*(download|डाउनलोड)", re.I)
TWO_K_ITEM = re.compile(r"2\s*K", re.I)
MIN_TILE_WIDTH = 180          # ignore small thumbnails such as the chat panel
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
PROGRESS_FILE = Path("flow_progress.json")
STOP = set("""a an the and with of in on at to from into over under by for as is are its his her their one two
three four five six seven eight nine ten seen shot light lighting mood action camera angle medium wide close eye
level slow while behind beside front back left right half dark side small large""".split())


# ------------------------------------------------------------------ prompts
def read_prompts(path):
    """Return {number: prompt}. Numbered lines keep their number, others are counted in order."""
    out, n = {}, 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", line)
        if m:
            n = int(m.group(1))
            out[n] = m.group(2).strip()
        else:
            n += 1
            out[n] = line
    if not out:
        sys.exit(f"No prompts found in {path}")
    return out


def words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOP}


def prompt_fingerprints(prompts):
    """Scene-specific words of each prompt (shared style text removed)."""
    fp = {n: words(re.split(r"\bStyle:", p)[0]) for n, p in prompts.items()}
    cnt = Counter(w for ws in fp.values() for w in ws)
    common = {w for w, c in cnt.items() if c > max(3, len(fp) * 0.4)}
    return {n: (ws - common) or ws for n, ws in fp.items()}


def match_prompt(text, fps):
    have = words(text)
    scored = sorted(((len(ws & have) / max(len(ws), 1), n) for n, ws in fps.items()), reverse=True)
    second = scored[1][0] if len(scored) > 1 else 0
    return scored[0][1], scored[0][0], scored[0][0] - second


# ------------------------------------------------------------------ progress
def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {"submitted": [], "downloaded": []}


def save_progress(p):
    PROGRESS_FILE.write_text(json.dumps(p, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ browser
def attach(port):
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
    except Exception:
        sys.exit("Could not attach to Chrome. Start Chrome with remote debugging first (see the guide).")
    page = next((pg for c in browser.contexts for pg in c.pages if "flow" in pg.url.lower()), None)
    if page is None:
        sys.exit("No Google Flow tab found. Open your Flow project in that Chrome window.")
    page.bring_to_front()
    print("Using tab:", page.url)
    return pw, page


def fail(page, what):
    shot = Path(f"flow_error_{int(time.time())}.png")
    try:
        page.screenshot(path=str(shot))
    except Exception:
        pass
    sys.exit(f"Stopped: {what}. Screenshot saved as {shot}.")


def visible(el):
    try:
        return el.is_visible()
    except Exception:
        return False


def label_of(el):
    try:
        return ((el.get_attribute("aria-label") or "") + " " + (el.get_attribute("placeholder") or "")
                + " " + (el.inner_text(timeout=300) or "")).strip()
    except Exception:
        return ""


def find_prompt_box(page):
    cands = []
    for sel in ("textarea", "[contenteditable=true]", "[role=textbox]", "input[type=text]"):
        for el in page.locator(sel).all():
            if visible(el):
                bb = el.bounding_box() or {"width": 0}
                cands.append((bool(PROMPT_BOX_HINT.search(label_of(el))), bb["width"], el))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return cands[0][2]


def click_by_text(page, pattern, timeout=5000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in ("[role=menuitem]", "[role=menuitemradio]", "button", "[role=option]", "li", "div[tabindex]"):
            for el in page.locator(sel).all():
                if not visible(el):
                    continue
                if pattern.search(label_of(el)):
                    try:
                        el.scroll_into_view_if_needed(timeout=1000)
                        el.hover(timeout=2000)
                        el.click(timeout=2000)
                        return True
                    except Exception:
                        try:                       # off-screen or covered: click it directly
                            el.dispatch_event("click")
                            return True
                        except Exception:
                            continue
        time.sleep(0.3)
    return False


# ------------------------------------------------------------------ generate
def split_style(prompt):
    """Return (scene part, style part) of a prompt that contains 'Style:'."""
    m = re.search(r"\bStyle:\s*", prompt)
    if not m:
        return prompt.strip(), ""
    return prompt[:m.start()].strip(), prompt[m.end():].strip()


def build_message(nums, prompts):
    scenes, styles = [], Counter()
    for n in nums:
        scene, style = split_style(prompts[n])
        scenes.append((n, scene))
        if style:
            styles[style] += 1
    shared = styles.most_common(1)[0][0] if styles else ""
    lines = [
        f"Generate exactly {len(nums)} images, one for each prompt below, in the order listed.",
        "Use each prompt exactly as written; do not rewrite or merge them.",
        "Make every image 16:9.",
        'Name each image exactly with its label, for example "Scene 004".',
        "Do not ask me any questions; generate all of them now.",
    ]
    if shared:
        lines += ["", f"Apply this style to every image: {shared}"]
    lines.append("")
    for n, scene in scenes:
        own_style = split_style(prompts[n])[1]
        extra = f" Style: {own_style}" if own_style and own_style != shared else ""
        lines.append(f"Scene {n:03d}: {scene}{extra}")
    return "\n".join(lines)


def find_send_button(page, box):
    bb = box.bounding_box() if box else None
    best, best_d = None, 1e9
    for b in page.locator("button").all():
        if not visible(b) or not SEND_BUTTON.search(label_of(b)):
            continue
        try:
            if b.is_disabled():
                continue
            r = b.bounding_box()
        except Exception:
            continue
        if r and bb:
            d = abs(r["x"] - (bb["x"] + bb["width"])) + abs(r["y"] - (bb["y"] + bb["height"]))
            if d < best_d:
                best, best_d = b, d
    return best if best_d < 800 else None


def box_text(box):
    try:
        return (box.input_value(timeout=500) if box.evaluate("e => 'value' in e") else box.inner_text(timeout=500)).strip()
    except Exception:
        return ""


def send_message(page, text):
    box = find_prompt_box(page)
    if box is None:
        fail(page, "could not find Flow's prompt box")
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    box.click()
    page.keyboard.insert_text(text)
    time.sleep(1.0)
    for attempt in ("button", "enter", "button"):
        if attempt == "button":
            btn = find_send_button(page, box)
            if btn is not None:
                try:
                    btn.click(timeout=3000)
                except Exception:
                    btn.dispatch_event("click")
            else:
                page.keyboard.press("Enter")
        else:
            box.click()
            page.keyboard.press("Enter")
        time.sleep(2.5)
        left = box_text(find_prompt_box(page) or box)
        if len(left) < 20:          # the box emptied, so the message went out
            return True
    fail(page, "the message stayed in the prompt box (send did not work)")


def page_state(page):
    try:
        return page.evaluate("""() => document.body.innerText.length + '|' +
            document.querySelectorAll('img').length""")
    except Exception:
        return ""


def agent_working(page):
    return any(visible(b) and STOP_BUTTON.search(label_of(b)) for b in page.locator("button").all())


def wait_for_agent(page, min_wait, quiet, timeout):
    """Done when the agent shows no Stop button and nothing on the page has changed for `quiet` seconds."""
    start = time.time()
    time.sleep(min_wait)
    last, still_since = page_state(page), time.time()
    while time.time() - start < timeout:
        time.sleep(5)
        now = page_state(page)
        if now != last or agent_working(page):
            last, still_since = now, time.time()
        elif time.time() - still_since >= quiet:
            return True
        mins = int(time.time() - start) // 60
        print(f"\r  waiting... {mins} min", end="", flush=True)
    return False


def cmd_generate(args, page, prompts, prog):
    if args.reset:
        prog["submitted"] = []
        save_progress(prog)
    todo = [n for n in sorted(prompts) if args.only is None or n in args.only]
    if not args.regenerate:
        todo = [n for n in todo if n not in prog["submitted"]]
    if not todo:
        print("Nothing to generate: every prompt was already sent (use --reset or --regenerate to redo).")
        return
    print(f"Generating {len(todo)} prompts, {args.batch} per message")
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        msg = build_message(batch, prompts)
        print(f"\nBatch {i // args.batch + 1}: scenes {batch[0]:03d}-{batch[-1]:03d} "
              f"({len(batch)} prompts, {len(msg):,} characters)")
        send_message(page, msg)
        prog["submitted"] = sorted(set(prog["submitted"]) | set(batch))
        save_progress(prog)
        print("  sent. Waiting for Flow to finish this batch...")
        ok = wait_for_agent(page, args.min_wait, args.quiet, args.batch_timeout)
        print("\r  batch finished            " if ok else "\r  time limit reached, sending the next batch")
    print("\nAll prompts sent.")


# ------------------------------------------------------------------ download
TEACH_FILE = Path("flow_teach.json")

# JavaScript helpers that run inside the Flow page
JS_RECORDER = """() => {
  if (window.__flowTeachOn) return;
  window.__flowTeachOn = true; window.__flowTeach = [];
  const pick = (el) => {
    for (let i = 0; el && i < 7; i++, el = el.parentElement) {
      const r = el.getAttribute && el.getAttribute('role');
      if (['BUTTON','A','LI'].includes(el.tagName) || ['button','menuitem','menuitemradio','option'].includes(r))
        return el;
    }
    return null;
  };
  document.addEventListener('pointerdown', (e) => {
    const el = pick(e.target); if (!el) return;
    let tileImg = null, box = el;
    for (let i = 0; box && i < 8 && !tileImg; i++, box = box.parentElement)
      for (const im of box.querySelectorAll('img')) { if (im.getBoundingClientRect().width >= 180) { tileImg = im; break; } }
    window.__flowTeach.push({
      tag: el.tagName, role: el.getAttribute('role') || '',
      aria: el.getAttribute('aria-label') || '', text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
      inTile: !!tileImg });
  }, true);
}"""

JS_FIND = """([d, x, y, insideTile]) => {
  const same = (el) => el.tagName === d.tag && (el.getAttribute('role') || '') === d.role &&
     (d.aria ? (el.getAttribute('aria-label') || '') === d.aria
             : (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80) === d.text);
  const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  let pool = [...document.querySelectorAll(d.tag + (d.role ? '[role="' + d.role + '"]' : ''))].filter(same);
  if (insideTile) {
    let el = document.elementFromPoint(x, y), tile = null;
    for (let i = 0; el && i < 8; i++, el = el.parentElement)
      if (pool.some(p => el.contains(p))) { tile = el; break; }
    pool = tile ? pool.filter(p => tile.contains(p)) : [];
  }
  pool = pool.filter(shown);
  if (!pool.length) return null;
  pool.sort((a, b) => { const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
     return (Math.abs(ra.x - x) + Math.abs(ra.y - y)) - (Math.abs(rb.x - x) + Math.abs(rb.y - y)); });
  const r = pool[0].getBoundingClientRect();
  return [r.x + r.width / 2, r.y + r.height / 2];
}"""

JS_FIND_TEXT = """([pattern, x, y]) => {
  const re = new RegExp(pattern, 'i');
  const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const pool = [...document.querySelectorAll('[role=menuitem],[role=menuitemradio],[role=option],button,li,a')]
     .filter(el => { const t = (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
                     return t.length > 0 && t.length < 50 && re.test(t) && shown(el); });
  if (!pool.length) return null;
  pool.sort((a, b) => { const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
     return (Math.abs(ra.x - x) + Math.abs(ra.y - y)) - (Math.abs(rb.x - x) + Math.abs(rb.y - y)); });
  const r = pool[0].getBoundingClientRect();
  return [r.x + r.width / 2, r.y + r.height / 2];
}"""

JS_TILE_LABEL = """([x, y]) => {
  let el = document.elementFromPoint(x, y);
  for (let i = 0; el && i < 7; i++, el = el.parentElement) {
    const t = (el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' +
              (el.getAttribute('title') || '') + ' ' + (el.getAttribute('alt') || '');
    if (/scene\\s*\\d+/i.test(t) && t.length < 400) return t;
  }
  return '';
}"""


def teach(page):
    print("\nTEACH STEP (only once). In the Chrome window with Flow:")
    print("  1. Hover over any image and click its three-dot menu (⋮)")
    print("  2. Move to Download (just hover, the 1K/2K/4K list opens)")
    print("  3. Click  2K Upscaled")
    print("One 2K file will download normally.\n")
    page.bring_to_front()
    page.evaluate(JS_RECORDER)
    page.evaluate("() => { window.__flowTeach = []; }")
    is_2k = lambda r: bool(TWO_K_ITEM.search(r["text"] + " " + r["aria"]))
    menu = twok = download = None
    deadline, seen = time.time() + 300, 0
    while time.time() < deadline and twok is None:
        time.sleep(0.4)
        try:
            rec = page.evaluate("() => window.__flowTeach || []")
        except Exception:
            rec = []
        for r in rec[seen:]:
            if not (r["text"] or r["aria"]):
                continue
            if menu is None:
                if is_2k(r):
                    continue                      # 2K before the menu: ignore
                menu = r
                print(f"  recorded three-dot menu -> {r['aria'] or r['text']!r}")
            elif is_2k(r):
                twok = r
                print(f"  recorded 2K Upscaled    -> {r['aria'] or r['text']!r}")
                break
            elif DOWNLOAD_ITEM.search(r["text"]) or "download" in (r["text"] + r["aria"]).lower():
                download = r
                print(f"  recorded Download       -> {r['aria'] or r['text']!r}")
            else:
                menu = r                          # clicked another menu first: use the latest one
                print(f"  recorded three-dot menu -> {r['aria'] or r['text']!r}")
        seen = len(rec)
    if menu is None or twok is None:
        sys.exit("Teach step timed out. Run the command again: click ⋮, hover Download, click 2K Upscaled.")
    teach_data = {"menu": menu, "download": download, "twok": twok}
    TEACH_FILE.write_text(json.dumps(teach_data, indent=1), encoding="utf-8")
    print(f"Saved to {TEACH_FILE}. Starting the downloads now.\n")
    time.sleep(4)
    page.keyboard.press("Escape")
    return teach_data


def find_target(page, desc, x, y, inside_tile=False, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            pos = page.evaluate(JS_FIND, [desc, x, y, inside_tile])
        except Exception:
            pos = None
        if pos:
            return pos
        time.sleep(0.3)
    return None


def images_in(folder):
    return {p.name for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT}


def visible_tiles(page):
    return page.evaluate(
        """(minW) => {
            const out = [];
            for (const img of document.querySelectorAll('img')) {
                const r = img.getBoundingClientRect();
                if (r.width < minW || r.height < 80) continue;
                if (r.bottom < 60 || r.top > innerHeight - 40) continue;
                out.push([img.currentSrc || img.src, r.left + r.width/2, r.top + r.height/2,
                          Math.round(r.top), Math.round(r.left)]);
            }
            out.sort((a, b) => (a[3] - b[3]) || (a[4] - b[4]));
            return out.map(o => [o[0], o[1], o[2]]);
        }""",
        MIN_TILE_WIDTH,
    )


def read_prompt_by_opening(page, x, y):
    before = page.inner_text("body")
    page.mouse.click(x, y)
    time.sleep(1.5)
    after = page.inner_text("body")
    new_lines = [l for l in after.splitlines() if l and l not in before]
    page.keyboard.press("Escape")
    time.sleep(0.8)
    return "\n".join(new_lines) if len(" ".join(new_lines)) > 40 else after


def download_tile(page, t, x, y, wait):
    """Hover the tile, click its ⋮, hover Download, click 2K. Returns the Playwright download."""
    page.mouse.move(x, y)
    time.sleep(0.7)
    pos = find_target(page, t["menu"], x, y, inside_tile=t["menu"].get("inTile", True))
    if pos is None:
        return None, "three-dot menu not found on this image"
    page.mouse.click(*pos)
    time.sleep(0.8)
    mpos = pos
    pos = find_target(page, t["download"], *mpos) if t.get("download") else None
    if pos is None:
        pos = page.evaluate(JS_FIND_TEXT, [r"^(download\s*)?download\b", *mpos])
    if pos is None:
        return None, "Download not found in the menu"
    page.mouse.move(*pos)
    time.sleep(0.9)                        # the 1K / 2K / 4K submenu opens on hover
    def find_2k():
        p2 = find_target(page, t["twok"], *pos, timeout=2.0)
        return p2 or page.evaluate(JS_FIND_TEXT, [r"^2\s*K\b", *pos])
    pos2 = find_2k()
    if pos2 is None:
        page.mouse.click(*pos)
        time.sleep(0.9)
        pos2 = find_2k()
    if pos2 is None:
        return None, "2K Upscaled not found in the Download menu"
    vh = page.evaluate("() => innerHeight")
    if not (0 < pos2[1] < vh):
        return None, "the 2K option opened off screen"
    # move sideways first so the submenu stays open, then click 2K
    page.mouse.move(pos2[0], pos[1], steps=8)
    page.mouse.move(*pos2, steps=8)
    try:
        with page.expect_download(timeout=wait * 1000) as info:
            page.mouse.click(*pos2)
        return info.value, ""
    except Exception:
        return None, f"no 2K file arrived within {wait:.0f}s"


def cmd_download(args, page, prompts, prog):
    folder = Path(args.downloads).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    t = None if args.teach or not TEACH_FILE.exists() else json.loads(TEACH_FILE.read_text(encoding="utf-8"))
    if t is None:
        t = teach(page)
    fps = prompt_fingerprints(prompts)
    page.mouse.move(400, 400)
    page.mouse.wheel(0, -100000)
    time.sleep(1.5)
    done, idle, saved, unmatched, problems = set(), 0, 0, 0, []
    while True:
        tiles = [tl for tl in visible_tiles(page) if tl[0] not in done]
        if not tiles:
            page.mouse.move(400, 400)
            page.mouse.wheel(0, 500)
            time.sleep(1.5)
            idle += 1
            if idle > 8:
                break
            continue
        idle = 0
        src, x, y = tiles[0]
        done.add(src)
        # bring the image toward the middle of the window so its menu fits on screen
        centre = page.evaluate("""(src) => {
            const im = [...document.querySelectorAll('img')].find(i => (i.currentSrc || i.src) === src);
            if (!im) return null;
            im.scrollIntoView({block: 'center', inline: 'nearest'});
            const r = im.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2];
        }""", src)
        if centre:
            time.sleep(0.6)
            x, y = centre

        # 1) name: the agent's "Scene ###" label on the tile, else the prompt
        page.mouse.move(x, y)
        time.sleep(0.6)
        lab = SCENE_LABEL.search(page.evaluate(JS_TILE_LABEL, [x, y]) or "")
        num = int(lab.group(1)) if lab and int(lab.group(1)) in prompts else None
        note = "Scene name"
        if num is None and not args.no_open:
            text = read_prompt_by_opening(page, x, y)
            lab = SCENE_LABEL.search(text)
            if lab and int(lab.group(1)) in prompts:
                num = int(lab.group(1))
            else:
                guess, score, margin = match_prompt(text, fps)
                if score >= args.min_score and margin >= 0.08:
                    num, note = guess, f"prompt match {score:.0%}"
        if num is None:
            unmatched += 1
            name, note = f"unmatched_{unmatched:03d}", "no Scene name found, check by hand"
        else:
            name = f"{num:03d}"
            if args.skip_existing and any((folder / f"{name}{e}").exists() for e in IMAGE_EXT):
                print(f"{name}  already downloaded, skipped")
                continue

        # 2) ⋮ -> Download -> 2K Upscaled (one retry if the file fails or is empty)
        dl, why = download_tile(page, t, x, y, args.wait)
        if dl is not None and dl.failure():
            why, dl = f"download failed ({dl.failure()})", None
        if dl is None:
            page.keyboard.press("Escape")
            time.sleep(2)
            dl, why = download_tile(page, t, x, y, args.wait)
        if dl is None:
            page.keyboard.press("Escape")
            problems.append(name)
            print(f"{name:<20} FAILED: {why}")
            if len(problems) >= 3 and saved == 0:
                fail(page, f"{why} (3 images in a row). Run again with --teach to redo the teach step")
            continue
        ext = Path(dl.suggested_filename or "x.png").suffix.lower() or ".png"
        target, k = folder / f"{name}{ext}", 2
        while target.exists():
            target = folder / f"{name}_v{k}{ext}"
            k += 1
        dl.save_as(str(target))
        if target.stat().st_size < 1000:
            target.unlink()
            page.keyboard.press("Escape")
            problems.append(name)
            print(f"{name:<20} FAILED: the 2K file was empty, will retry on the next run")
            continue
        saved += 1
        print(f"{target.name:<20} {note}")
        if name[:3].isdigit():
            prog["downloaded"] = sorted(set(prog["downloaded"]) | {int(name[:3])})
            save_progress(prog)
        page.keyboard.press("Escape")
        if args.limit and saved >= args.limit:
            break
        time.sleep(args.pause)

    have = {int(m.group(1)) for f in images_in(folder) if (m := re.match(r"^(\d+)", f))}
    missing = [n for n in sorted(prompts) if n not in have]
    Path("missing.txt").write_text(",".join(str(n) for n in missing), encoding="utf-8")
    print(f"\nSaved {saved} images in 2K ({unmatched} unmatched, {len(problems)} failed) in {folder}")
    if missing:
        print(f"{len(missing)} scenes have no file yet: {', '.join(f'{n:03d}' for n in missing[:40])}"
              f"{' ...' if len(missing) > 40 else ''}")
        print("Run the same download command again to retry; finished files are skipped.")


# ------------------------------------------------------------------ main
def parse_only(value):
    if value is None:
        return None
    text = Path(value).read_text(encoding="utf-8") if Path(value).exists() else value
    nums = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if "-" in part:
            a, b = part.split("-")
            nums |= set(range(int(a), int(b) + 1))
        elif part:
            nums.add(int(part))
    return nums


def main():
    ap = argparse.ArgumentParser(description="Bulk-generate and 2K-download Google Flow images.")
    ap.add_argument("step", choices=["generate", "download", "all"])
    ap.add_argument("--prompts", required=True, help="text file with your prompts, one per line")
    ap.add_argument("--downloads", help="folder to save the 2K images in (needed for download/all)")
    ap.add_argument("--batch", type=int, default=20, help="prompts per message (Flow takes about 20-25)")
    ap.add_argument("--min-wait", type=float, default=60, help="seconds to wait before checking a batch")
    ap.add_argument("--quiet", type=float, default=45, help="seconds of no change that mean a batch is done")
    ap.add_argument("--batch-timeout", type=float, default=1800, help="max seconds to wait for one batch")
    ap.add_argument("--reset", action="store_true", help="forget which prompts were already sent")
    ap.add_argument("--only", help="numbers to do, e.g. 5,9,40-60 or a file such as missing.txt")
    ap.add_argument("--regenerate", action="store_true", help="send prompts again even if sent before")
    ap.add_argument("--limit", type=int, default=0, help="download only N images (use 2 for a test)")
    ap.add_argument("--wait", type=float, default=120, help="seconds to wait for each 2K file")
    ap.add_argument("--min-score", type=float, default=0.5, help="how sure a prompt match must be (0-1)")
    ap.add_argument("--teach", action="store_true", help="redo the one-time teach step for the download menu")
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between downloads")
    ap.add_argument("--no-open", action="store_true", help="never open an image to read its prompt")
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                    help="download again even if NNN.png already exists")
    ap.add_argument("--port", type=int, default=9222)
    args = ap.parse_args()
    args.only = parse_only(args.only)
    if args.step in ("download", "all") and not args.downloads:
        sys.exit("--downloads is required for this step")

    prompts = read_prompts(args.prompts)
    print(f"{len(prompts)} prompts loaded ({min(prompts):03d}-{max(prompts):03d})")
    prog = load_progress()
    pw, page = attach(args.port)
    try:
        if args.step in ("generate", "all"):
            cmd_generate(args, page, prompts, prog)
        if args.step in ("download", "all"):
            cmd_download(args, page, prompts, prog)
    finally:
        pw.stop()


if __name__ == "__main__":
    main()
