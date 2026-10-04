#!/usr/bin/env python3
"""Decode reference GIF corpora with luce-gif and compare every composited frame.

Two oracles:

- the pygif test suite (https://github.com/robert-ancell/pygif, test-suite/), whose .conf
  files give each image's frames as RGBA, its loop count and its delays, rendered the way
  browsers render them (disposal to transparent, first frame on a transparent canvas);
- Python Pillow, run here, for every GIF of the corpora: the pygif suite, Pillow's own
  Tests/images, Ladybird's LibGfx test inputs and giflib's pic/.

Pixels compare exactly, except that two fully transparent pixels are equal whatever
their colour. The corpora live in ../.donors (never in this repository):

  ../.donors/gif-corpus/pygif/test-suite
  ../.donors/pillow-tests/Tests/images
  ../.donors/ladybird-pin/Tests/LibGfx/test-inputs/gif
  ../.donors/gif-corpus/giflib/pic

  python3 tools/conformance.py [--list-failing] [--python PYTHON-WITH-PILLOW]

LUCE_BASE names the compiler (default: luce-base on PATH). Pillow runs in the Python
given by --python (default: this one), so a venv with Pillow can be named.
"""
import argparse, configparser, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONORS = ROOT.parent / ".donors"
PYGIF = DONORS / "gif-corpus/pygif/test-suite"
CORPORA = {
    "pygif": PYGIF,
    "pillow": DONORS / "pillow-tests/Tests/images",
    "ladybird": DONORS / "ladybird-pin/Tests/LibGfx/test-inputs/gif",
    "giflib": DONORS / "gif-corpus/giflib/pic",
}
TOOL = ROOT / "build/gifcheck"

# Where we differ from an oracle on purpose, following browsers (Chrome, whose GIF decoder
# is Wuffs, and Firefox): file name -> why.
FRAMES = "every image is a frame, as in browsers (a zero delay plays as 100 ms); the suite merges images without a delay"
WIDEN = "the first frame widens a screen too small for it, as Chrome (Wuffs) and Firefox do"
TRANSPARENT = "browsers leave undrawn and background-disposed areas transparent; Pillow paints an opaque palette colour"
BAD_CODE = "a bad LZW code or code size ends the frame's pixels and keeps the frame, transparent where undrawn"
LENIENT = "Pillow rejects the file; we decode what it holds, as browsers do"
DELIBERATE = {
    "pygif": {
        "animation-multi-image-explicit-zero-delay.gif": FRAMES, "animation-multi-image.gif": FRAMES,
        "dispose-restore-previous.gif": FRAMES, "high-color.gif": FRAMES, "images-combine.gif": FRAMES,
        "images-overlap.gif": FRAMES,
        "gif87a-animation.gif": "without a loop extension browsers play once; the suite loops GIF87a animations",
        "image-outside-bg.gif": WIDEN, "image-overlap-bg.gif": WIDEN,
        "image-zero-height.gif": "the file ends inside the local palette: no frame",
        "image-zero-size.gif": "the file ends inside the local palette: no frame",
        "invalid-code.gif": BAD_CODE, "overflow-codes.gif": BAD_CODE, "overflow-codes-max.gif": BAD_CODE,
        "invalid-colors.gif": "an index past the palette is opaque black (Wuffs, Firefox; Pillow agrees)",
        "no-data.gif": "a GIF without an image fails to decode, as browsers show it broken",
        "plain-text.gif": "the plain-text extension is skipped and the image drawn, as browsers do",
    },
    "pillow": {
        "pillow/dispose_bgnd.gif": TRANSPARENT, "pillow/dispose_bgnd_rgba.gif": TRANSPARENT,
        "pygif/dispose-restore-background.gif": TRANSPARENT, "pygif/high-color.gif": TRANSPARENT,
        "pygif/image-inside-bg.gif": TRANSPARENT, "pygif/image-outside-bg.gif": TRANSPARENT,
        "pygif/image-overlap-bg.gif": TRANSPARENT, "pygif/images-combine.gif": TRANSPARENT,
        "pygif/missing-pixels.gif": TRANSPARENT,
        "pillow/decompression_bomb_extents.gif": "Pillow widens the canvas for any frame; browsers for the first only",
        "pillow/test_extents.gif": "Pillow widens the canvas for any frame; browsers for the first only",
        "pillow/test_extents_transparency.gif": "a second GIF follows the first's data: browsers end at a byte that starts no "
                                                "block, Pillow skips ahead to the next image",
        "pillow/zero_width.gif": LENIENT, "pygif/image-zero-width.gif": LENIENT, "pygif/invalid-code.gif": LENIENT,
        "pygif/overflow-codes.gif": LENIENT, "pygif/overflow-codes-max.gif": LENIENT, "ladybird/minimal-1x1.gif": LENIENT,
        "pygif/loop-animexts.gif": "the ANIMEXTS1.0 loop count is read, as Wuffs and Firefox read it",
    },
}

# Pillow's frames as JSON on stdout: per file, an error, or the size, loop, durations and
# the frames' RGBA written to a file.
PILLOW = r"""
import json, sys
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
out = {}
for index, path in enumerate(sys.argv[2:]):
    try:
        with Image.open(path) as image:
            frames, durations = [], []
            for n in range(getattr(image, "n_frames", 1)):
                image.seek(n)
                frames.append(image.convert("RGBA").tobytes())
                durations.append(image.info.get("duration", 0))
            target = f"{sys.argv[1]}/{index}.pillow"
            open(target, "wb").write(b"".join(frames))
            out[path] = {"size": list(image.size), "frames": len(frames), "loop": image.info.get("loop"),
                         "durations": durations, "rgba": target}
    except Exception as failure:
        out[path] = {"error": f"{type(failure).__name__}: {failure}"}
print(json.dumps(out))
"""


def build():
    base = os.environ.get("LUCE_BASE", "luce-base")
    TOOL.parent.mkdir(exist_ok=True)
    subprocess.run([base, "build", str(ROOT / "tools/gifcheck.lucb"), "-o", str(TOOL), "--release"], cwd=ROOT, check=True)


def decode(paths, directory):
    """Run gifcheck over `paths`: per path, a dict of what it reported, frames included."""
    listing = Path(directory) / "list.txt"
    listing.write_text("".join(f"- {p}\n" for p in paths))
    result = subprocess.run([str(TOOL), str(listing), directory], capture_output=True, text=True, timeout=600)
    found = {}
    for line in result.stdout.splitlines():
        number, status, *rest = line.split(" ", 2)
        path = paths[int(number)]
        if status != "ok":
            found[path] = {"error": " ".join(rest)}
            continue
        width, height, frames, loop, delays, durations, disposals = rest[0].split(" ") + rest[1:]
        data = (Path(directory) / f"{number}.rgba").read_bytes()
        found[path] = {"size": [int(width), int(height)], "frames": int(frames), "loop": None if loop == "-" else int(loop),
                       "delays": [int(d) for d in delays.split(",")], "durations": [int(d) for d in durations.split(",")],
                       "disposals": disposals, "rgba": data}
    if result.returncode != 0:
        sys.exit(f"gifcheck failed:\n{result.stderr}")
    return found


def differing(a, b):
    """How many pixels of the RGBA buffers `a` and `b` differ, two transparent ones equal."""
    count = 0
    for at in range(0, min(len(a), len(b)), 4):
        if a[at + 3] == 0 and b[at + 3] == 0:
            continue
        if a[at:at + 4] != b[at:at + 4]:
            count += 1
    return count


def compare_frames(ours, theirs, width, height):
    """A short verdict on two runs of frames of one size: '' when they match."""
    size = width * height * 4
    if len(ours) != len(theirs):
        return f"{len(ours) // max(size, 1)} frames, expected {len(theirs) // max(size, 1)}"
    bad = []
    for frame in range(len(ours) // max(size, 1)):
        count = differing(ours[frame * size:(frame + 1) * size], theirs[frame * size:(frame + 1) * size])
        if count:
            bad.append(f"frame {frame}: {count} px")
    return "; ".join(bad[:4]) + (f" (+{len(bad) - 4} frames)" if len(bad) > 4 else "")


def pygif_expectations():
    """The suite's expectations: per GIF path, size, frames' RGBA, loop and delays."""
    expected = {}
    for conf in sorted(PYGIF.glob("*.conf")):
        config = configparser.ConfigParser()
        config.read(conf)
        c = config["config"]
        names = [n for n in c["frames"].split(",") if n]
        width, height = int(c["width"]), int(c["height"])
        frames = b"".join((PYGIF / config[n]["pixels"]).read_bytes() for n in names)
        loop = c.get("loop-count", "0")
        expected[str(PYGIF / c["input"])] = {
            "size": [width, height], "frames": len(names), "rgba": frames,
            "loop": 0 if loop == "infinite" else (int(loop) or None),
            "delays": [int(config[n].get("delay", "0")) for n in names]}
    return expected


def against_pygif(ours):
    rows = []
    for path, expect in pygif_expectations().items():
        got = ours.get(path, {"error": "not run"})
        name = Path(path).name
        if "error" in got:
            verdict = "" if expect["frames"] == 0 else f"error: {got['error']}"
            rows.append((name, verdict, ""))
            continue
        problems = []
        if expect["frames"] == 0:
            problems.append(f"decoded {got['frames']} frames, suite expects none")
        else:
            if got["size"] != expect["size"]:
                problems.append(f"size {got['size']} expected {expect['size']}")
            else:
                verdict = compare_frames(got["rgba"], expect["rgba"], *expect["size"])
                if verdict:
                    problems.append(verdict)
            if got["loop"] != expect["loop"]:
                problems.append(f"loop {got['loop']} expected {expect['loop']}")
            if got["delays"][:expect["frames"]] != expect["delays"]:
                problems.append(f"delays {got['delays']} expected {expect['delays']}")
        rows.append((name, "; ".join(problems), ""))
    return rows


def against_pillow(ours, pillow):
    rows = []
    for path, theirs in pillow.items():
        got = ours.get(path, {"error": "not run"})
        corpus = next(k for k, v in CORPORA.items() if path.startswith(str(v)))
        name = f"{corpus}/{Path(path).name}"
        if "error" in theirs and "error" in got:
            rows.append((name, "", "both reject"))
        elif "error" in theirs:
            rows.append((name, f"Pillow rejects ({theirs['error'][:60]}), we decode {got['frames']} frames", ""))
        elif "error" in got:
            rows.append((name, f"we reject ({got['error'][:60]}), Pillow decodes", ""))
        else:
            problems = []
            if got["size"] != theirs["size"]:
                problems.append(f"size {got['size']} vs Pillow {theirs['size']}")
            else:
                verdict = compare_frames(got["rgba"], Path(theirs["rgba"]).read_bytes(), *got["size"])
                if verdict:
                    problems.append(verdict)
            if got["loop"] != theirs["loop"]:
                problems.append(f"loop {got['loop']} vs Pillow {theirs['loop']}")
            if [d * 10 for d in got["delays"]] != theirs["durations"]:
                problems.append("delays differ")
            rows.append((name, "; ".join(problems), ""))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-failing", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-build", action="store_true")
    options = parser.parse_args()
    if not options.no_build:
        build()
    paths = sorted(str(p) for corpus in CORPORA.values() for p in corpus.glob("*.gif"))
    with tempfile.TemporaryDirectory() as directory:
        ours = decode(paths, directory)
        result = subprocess.run([options.python, "-c", PILLOW, directory, *paths], capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            sys.exit(f"Pillow failed:\n{result.stderr}")
        pillow = json.loads(result.stdout)
        tables = [("pygif", "pygif suite (browser-style expectations)", against_pygif(ours)),
                  ("pillow", "Pillow 12, every corpus", against_pillow(ours, pillow))]
    unexplained = 0
    for key, title, rows in tables:
        known = DELIBERATE[key]
        matched = [r for r in rows if not r[1]]
        deliberate = [r for r in rows if r[1] and r[0] in known]
        failing = [r for r in rows if r[1] and r[0] not in known]
        unexplained += len(failing)
        print(f"\n{title}: {len(rows)} files, {len(matched)} match, {len(deliberate)} differ on purpose, {len(failing)} unexplained")
        for name, problem, _ in failing:
            print(f"  DIFF {name}: {problem}")
        if options.list_failing:
            for name, problem, _ in deliberate:
                print(f"  on purpose {name}: {known[name]} [{problem}]")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
