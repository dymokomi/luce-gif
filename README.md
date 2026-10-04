# luce-gif

A GIF decoder for Luce/Base: GIF87a and GIF89a, still pictures and animations, every
frame handed back as a whole canvas, composited as browsers show it. It depends on no
other package: only the compiler's built-in modules. The browser engine and luce-image
decode GIFs through it.

```luce
from luce_gif import gif

let found = try gif.info(data)                 # canvas size, frame count, loop count
let pixels = try alloc u8[found.width * found.height * 4]
try gif.decode_rgba8(data, pixels)             # the first frame, straight RGBA

var animation = try gif.Animation.open(data)   # `data` must outlive it
defer animation.release()
for (index, frame) in animation.frames().indexed():
    try animation.render(index, pixels)        # frame `index`, everything before it composited
    show(pixels, frame.duration)               # milliseconds, as browsers play it
```

## API

- `info(data) -> Info!`: `width` and `height` of the canvas, `frame_count`, `loop_count`
  (`u32?`: the NETSCAPE2.0 or ANIMEXTS1.0 value as written, 0 forever, none without one),
  `version` (87 or 89), `alpha` (whether the canvas can show transparent pixels);
  `plays()` is how many times browsers play it in all (0 forever, 1 without a loop
  extension, else the count + 1), `animated()` whether it has more than one frame.
- `decode_rgba8(data, out, options)`: the first frame on a transparent canvas, into
  `out` of at least `width * height * 4` bytes.
- `Animation.open(data, options) -> Animation!`, then `frames()` and
  `render(index, out)`: each `Frame` has its rectangle (`left`, `top`, `width`,
  `height`), `interlaced`, `delay` (hundredths of a second, as written), `duration`
  (milliseconds as browsers play it), `disposal` (`keep`, `background`, `previous`) and
  `transparent` (`u8?`). `render` disposes of the frame before, then draws the frame over
  what is left, and copies the canvas out. Frames render fastest in order; an earlier
  index starts again from the first frame. `release` frees the frame list and canvases.
- `Options.max_pixels` (default 2^28) bounds the canvas; an `Animation` holds one canvas
  and, for frames disposed "previous", a second.
- Errors: `corrupt` (not a GIF, or damaged before the first image), `limit` (the canvas
  is over `max_pixels`), `invalid` (an output buffer too small, a frame index past the
  last).

## Browser behaviour

GIFs in the wild are often damaged, and browsers agree on how to show them; this decoder
follows Chrome (whose GIF decoder is Wuffs) and Firefox:

- The canvas starts transparent; the background colour index is ignored. Disposal 2
  clears the frame's area to transparent; 3, and 4, restore it; 0, 1 and 5–7 keep it.
- A delay of 0 or 1 hundredths plays as 100 ms (`duration`); `delay` keeps the value
  written.
- The first frame widens a logical screen too small for it; later frames are clipped.
- A file that ends early keeps its frames, the last drawn as far as its data goes; a
  descriptor or palette cut short drops that frame. A byte that starts no block ends the
  file, as the trailer does. Comments, plain text and other application extensions are
  skipped.
- An LZW code the table does not hold yet ends the frame's pixels; the rest of the frame
  is undrawn. Minimum code sizes 1 to 11 are read (0 and 12 draw nothing). Indices past
  the palette, or with no palette at all, are opaque black.
- The first loop extension counts; later ones are ignored.

## Tests

```
./test.sh                                   # unit tests, native and C, every compiler; -W, fmt
LUCE_BASE_EXTRA=~/.local/bin/luce-base ./test.sh     # and a second toolchain
python3 tools/conformance.py --python VENV/bin/python --list-failing
python3 tools/fuzz.py --cases 20000 [--guard-malloc]
```

The unit tests (`tests/gif/`) write their GIFs with a small builder (literal LZW codes,
with and without clear codes) beside one Pillow-encoded picture. `tools/gifcheck.lucb`
decodes a list of files and writes every frame; `tools/conformance.py` compares the
frames with two oracles on the corpora kept outside the repository in `../.donors/`:
the pygif test suite's expected frames (rendered as browsers render them) and Python
Pillow's on every GIF of the pygif suite, Pillow's test images, Ladybird's LibGfx test
inputs and giflib's samples:

| Oracle | Files | Match | Differ on purpose | Unexplained |
| --- | ---: | ---: | ---: | ---: |
| pygif suite | 84 | 67 | 17 | 0 |
| Pillow 12.3 | 133 | 114 | 19 | 0 |

The deliberate differences, each listed with its reason by `--list-failing`:

- pygif merges images without a delay into one frame; browsers (and this decoder) show
  each image as a frame (6 files). pygif loops a GIF87a animation without a loop
  extension, and draws nothing for a frame with a bad code, a code size of 12 or more, a
  plain-text extension, or indices past the palette; browsers play once, keep the
  frame, skip the extension and draw black.
- Pillow paints undrawn and background-disposed areas with an opaque palette colour
  where browsers leave them transparent (9 files); it widens the canvas for any frame,
  not only the first (2); it skips junk between blocks to find a later image (1); it
  rejects truncated and zero-width files browsers show (6); it ignores ANIMEXTS1.0 (1).

`tools/fuzz.py` mutates the corpora (20,000 cases: bit flips, insertions, truncations,
spliced chunks, GIF's own block bytes) and decodes every frame of each under a time
limit, after a stress phase of adversarial files (a 65535 x 65535 frame, 20,000 frames,
a 4096 x 1024 picture of long LZW strings, a stream of clear codes, "previous" disposal
on a 2048 x 2048 canvas, each well under two seconds): no traps, no hangs, the peak
memory of a decoding process 91 MB. Under Guard Malloc (`--guard-malloc`, and the unit
tests run with `DYLD_INSERT_LIBRARIES=/usr/lib/libgmalloc.dylib`) nothing is reported.
