#!/usr/bin/env python3
"""Mutate GIFs and decode them: the decoder must never trap, hang or grow without bound,
whatever the bytes.

Each case is one of the corpus files (see conformance.py) changed by a few random
mutations: bytes flipped, inserted, deleted or repeated, chunks spliced in from another
file, and GIF's own structures (block introducers, extension labels, sub-block sizes,
code sizes, 16-bit fields set to 0 or 65535) dropped in at random places. Cases run in
batches through build/gifcheck (tools/gifcheck.lucb, with `qs`: every frame rendered,
nothing written, a 4-megapixel canvas limit), each batch under a time limit; a batch that
dies or times out is narrowed to the case responsible, which is saved under
build/fuzz-failures. The peak memory of the decoding processes is reported.

First, a stress phase decodes generated files that are large or adversarial: a frame of
65535 x 65535 pixels with little data, 20,000 frames, a 4096 x 1024 picture of long LZW
strings, a stream of clear codes, and frames disposed "previous" over a large canvas. Each
must finish in under two seconds.

  python3 tools/fuzz.py [--cases 20000] [--seed 1] [--guard-malloc]
"""
import argparse, os, random, resource, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from conformance import CORPORA  # noqa: E402

TOOL = ROOT / "build/gifcheck"
FAILURES = ROOT / "build/fuzz-failures"
# With --guard-malloc, the tool runs under macOS's Guard Malloc (every allocation on its
# own page, freed pages unmapped), which turns an overrun or a use after free into a crash.
ENVIRONMENT = dict(os.environ)
TOKENS = [b"\x2c", b"\x21\xf9\x04", b"\x21\xff\x0bNETSCAPE2.0\x03\x01", b"\x21\xfe", b"\x21\x01\x0c", b"\x3b",
          b"\x00", b"\xff", b"\x02", b"\x08", b"\x0b", b"\x0c", b"\xff\xff", b"\x00\x00", b"\x80", b"\x87", b"\x40",
          b"\xff" * 64, b"\x00" * 64, b"GIF89a", b"\x21\xf9\x04\x0d\x00\x00\x00\x00"]


def lzw(indices, minimum):
    """`indices` LZW-encoded as GIF's sub-blocks, a clear code whenever the table fills."""
    clear = 1 << minimum
    out, bits, held = bytearray(), 0, 0
    table, prefix = {}, None
    size, decoded, first = minimum + 1, clear + 2, True  # the decoder's code width and table

    def emit(code):
        nonlocal bits, held, size, decoded, first
        bits |= code << held
        held += size
        while held >= 8:
            out.append(bits & 255)
            bits >>= 8
            held -= 8
        if code == clear:
            size, decoded, first = minimum + 1, clear + 2, True
        elif code != clear + 1:
            if not first and decoded < 4096:
                decoded += 1
                if decoded == 1 << size and size < 12:
                    size += 1
            first = False

    emit(clear)
    for index in indices:
        if prefix is None:
            prefix = index
        elif (prefix, index) in table:
            prefix = table[(prefix, index)]
        else:
            emit(prefix)
            table[(prefix, index)] = clear + 2 + len(table)
            prefix = index
            if len(table) + clear + 2 >= 4096:
                emit(prefix)
                emit(clear)
                table, prefix = {}, None
    if prefix is not None:
        emit(prefix)
    emit(clear + 1)
    if held:
        out.append(bits)
    return blocks(bytes(out))


def blocks(data):
    return b"".join(bytes([len(data[at:at + 255])]) + data[at:at + 255] for at in range(0, len(data), 255)) + b"\x00"


def gif(width, height, body, palette=b"\x00\x00\x00\xff\x00\x00\x00\xff\x00\x00\x00\xff"):
    bits = max(0, (len(palette) // 3 - 1).bit_length() - 1)
    return b"GIF89a" + struct.pack("<HHBBB", width, height, 0x80 | bits, 0, 0) + palette + body + b"\x3b"


def image(left, top, width, height, data, minimum=2):
    return b"\x2c" + struct.pack("<HHHHB", left, top, width, height, 0) + bytes([minimum]) + data


def stress_files():
    """Large and adversarial GIFs: (name, bytes)."""
    yield "huge frame", gif(64, 64, image(0, 0, 65535, 65535, lzw([1] * 100000, 2)))
    yield "many frames", gif(64, 64, (b"\x21\xf9\x04\x08\x00\x00\x00\x00" + image(0, 0, 64, 64, lzw([2] * 8, 2))) * 20000)
    palette = bytes(index for index in range(256) for _ in range(3))
    smooth = [(x // 64 + y // 16) % 256 for y in range(1024) for x in range(4096)]
    yield "string codes", gif(4096, 1024, image(0, 0, 4096, 1024, lzw(smooth, 8), 8), palette=palette)
    yield "clear codes", gif(16, 16, image(0, 0, 16, 16, blocks(bytes.fromhex("244992") * 300000)))
    previous = (b"\x21\xf9\x04\x0c\x00\x00\x00\x00" + image(0, 0, 2048, 2048, lzw([3] * 4, 2))) * 200
    yield "disposal previous", gif(2048, 2048, previous)


def stress():
    """Decode the stress files; answer the failures."""
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        for name, data in stress_files():
            path = Path(directory) / "stress.gif"
            path.write_bytes(data)
            started = time.monotonic()
            done, crashed, trailer = run([("qs", str(path))], timeout=30)
            spent = time.monotonic() - started
            verdict = "trap" if crashed else ("slow" if spent > 2 else "ok")
            print(f"stress {name:<20} {len(data) / 1e6:6.2f} MB {spent:6.2f} s  {verdict}", flush=True)
            if verdict != "ok":
                failures.append(f"stress {name}: {verdict} {trailer.strip()[-200:]}")
    return failures


def mutate(data, rng, donors):
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        choice = rng.random()
        at = rng.randint(0, len(data))
        if choice < 0.25 and data:
            data[min(at, len(data) - 1)] ^= 1 << rng.randint(0, 7)
        elif choice < 0.4:
            data[at:at] = rng.choice(TOKENS)
        elif choice < 0.5 and data:
            del data[at:at + rng.randint(1, 16)]
        elif choice < 0.6 and data:
            del data[at:]
        elif choice < 0.7 and data:
            end = min(len(data), at + rng.randint(1, 64))
            data[at:at] = data[at:end] * rng.randint(1, 8)
        elif choice < 0.85:
            donor = rng.choice(donors)
            start = rng.randint(0, max(0, len(donor) - 1))
            data[at:at] = donor[start:start + rng.randint(1, 200)]
        elif data:
            data[min(at, len(data) - 1)] = rng.choice([0, 1, 2, 7, 8, 11, 12, 0x80, 0xff])
    return bytes(data)


def run(jobs, timeout):
    """Run `jobs` [(flags, path)] in one tool process: (finished count, crashed?, stderr)."""
    with tempfile.TemporaryDirectory() as directory:
        listing = Path(directory) / "list.txt"
        listing.write_text("".join(f"{flags} {path}\n" for flags, path in jobs))
        try:
            result = subprocess.run([str(TOOL), str(listing), directory], capture_output=True, timeout=timeout, env=ENVIRONMENT)
        except subprocess.TimeoutExpired as expired:
            return len((expired.stdout or b"").splitlines()), True, "timeout"
        return len(result.stdout.splitlines()), result.returncode != 0, result.stderr.decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--no-stress", action="store_true")
    parser.add_argument("--guard-malloc", action="store_true")
    options = parser.parse_args()
    if options.guard_malloc:
        ENVIRONMENT["DYLD_INSERT_LIBRARIES"] = "/usr/lib/libgmalloc.dylib"
    stressed = [] if options.no_stress else stress()
    rng = random.Random(options.seed)
    seeds = sorted(p for corpus in CORPORA.values() for p in corpus.glob("*.gif") if p.stat().st_size < 200000)
    donors = [p.read_bytes() for p in seeds]
    failures = []
    done, crashed, trailer = run([("qs", str(seed)) for seed in seeds], timeout=600)
    print(f"corpus: {len(seeds)} files, {'crashed: ' + trailer.strip()[-300:] if crashed else 'no traps'}", flush=True)
    if crashed:
        failures.append(f"corpus file {seeds[done]}: {trailer.strip()[-300:]}")
    with tempfile.TemporaryDirectory() as work:
        made = 0
        while made < options.cases:
            count = min(options.batch, options.cases - made)
            jobs = []
            for index in range(count):
                path = Path(work) / f"{made + index}.gif"
                path.write_bytes(mutate(rng.choice(donors), rng, donors))
                jobs.append(("qs", str(path)))
            start = 0
            while start < len(jobs):
                done, crashed, trailer = run(jobs[start:], timeout=120)
                if not crashed:
                    break
                bad = start + done
                FAILURES.mkdir(parents=True, exist_ok=True)
                saved = FAILURES / f"case-{made + bad}.gif"
                shutil.copy(jobs[bad][1], saved)
                failures.append(f"{saved}: {trailer.strip()[-300:]}")
                print(f"FAIL {failures[-1]}", flush=True)
                start = bad + 1
            made += count
            for path in Path(work).iterdir():
                path.unlink()
            print(f"{made} cases, {len(failures)} failing", flush=True)
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    print(f"{options.cases} cases: {len(failures)} traps or hangs; peak memory of a decoding process {peak_mb:.1f} MB")
    for line in stressed:
        print(f"FAIL {line}")
    return 1 if failures or stressed else 0


if __name__ == "__main__":
    sys.exit(main())
