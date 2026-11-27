#!/usr/bin/env python3
# midiroll project (.mrproj) -> smolt music MML, meant to be read and edited by hand.
#
#   ./tools/mrproj2mml.py song.mrproj > song.mml
#
# - each midiroll instrument gets one track per note it plays at the same time:
#   the highest note of a chord goes to the instrument's first track
# - one line per bar, beats separated by a space, bar numbers as comments
# - the tempo is snapped so a row is a whole number of frames (60 fps)
# - the $ instruments are a rough translation, to be tuned by hand
import math
import sys

FPS = 60
NOTE_NAMES = ["c", "c+", "d", "d+", "e", "f", "f+", "g", "g+", "a", "a+", "b"]
WAVES = "PSTN"  # midiroll: pulse, saw, triangle, noise


def load(path):
    lines = open(path).read().split("\n")
    assert lines[0].startswith("MIDIROLL"), "not a midiroll project"
    i = 1
    p = {"insts": [], "notes": []}
    while i < len(lines):
        f = lines[i].split()
        i += 1
        if not f: continue
        if f[0] == "bpm": p["bpm"] = float(f[1])
        elif f[0] == "grid": p["grid"], p["triplet"] = int(f[1]), f[2] == "1"
        elif f[0] == "inst":
            for _ in range(int(f[1])):
                g = lines[i].split(None, 10)
                i += 1
                inst = dict(zip("hue pw pw_sweep vol a d s r".split(), map(float, g[:8])))
                inst["loop"], n, inst["name"] = int(g[8]), int(g[9]), g[10] if len(g) > 10 else ""
                inst["table"] = [tuple(map(int, lines[i + k].split())) for k in range(n)]
                i += n
                p["insts"].append(inst)
        elif f[0] == "notes":
            for _ in range(int(f[1])):
                s, l, pitch, inst, vel = lines[i].split()[:5]
                i += 1
                p["notes"].append((int(s), int(l), int(pitch), int(inst), float(vel)))
    return p


# smolt's envelope rates: a/d/r n take about 1 + (n + 8) * n * 15 / 128 frames
def env_rate(seconds):
    frames = seconds * FPS
    return min(range(64), key=lambda n: abs(1 + (n + 8) * n * 15 / 128 - frames))


def instrument(k, inst):
    out = []
    if inst["a"] > 0.5 / FPS: out.append(f"a{env_rate(inst['a'])}")
    out.append(f"d{env_rate(inst['d'])} s{round(inst['s'] * 64)} r{env_rate(inst['r'])}")
    out.append(f"v{round(inst['vol'] * 64)}")
    out.append(f"W{round(inst['pw'] * 256)}")
    # midiroll sweeps per 50 Hz tick, in 0..1; smolt per frame, in 1/1024
    if inst["pw_sweep"]: out.append(f"w{round(inst['pw_sweep'] * 1024 * 50 / FPS)}")
    rows, wave = [], None
    for j, (w, absolute, pitch) in enumerate(inst["table"] or [(0, 0, 0)]):
        r = "|" if j == inst["loop"] and 0 < len(inst["table"]) - 1 else ""
        if w != wave: r += WAVES[w]
        wave = w
        r += f"={pitch - 12}" if absolute else f"~{pitch}"
        rows.append(r + ",")
    return f"# {inst['name']}\n${k} {' '.join(out)} {''.join(rows)}"


def main():
    p = load(sys.argv[1])
    beat_us = 60e6 / p["bpm"]
    grid_us = beat_us * 4 / p["grid"] * (2 / 3 if p["triplet"] else 1)
    bar_grid = round(beat_us * 4 / grid_us)

    # quantize to the grid, then use the coarsest row that still fits every note and the bar
    notes = sorted({(round(s / grid_us), max(1, round((s + l) / grid_us) - round(s / grid_us)), pitch, inst, vel)
                    for s, l, pitch, inst, vel in p["notes"]}, key=lambda n: (n[0], -n[2]))
    step = bar_grid
    for s, l, *_ in notes: step = math.gcd(step, s, l)
    notes = [(s // step, l // step, pitch, inst, vel) for s, l, pitch, inst, vel in notes]
    row_bar = bar_grid // step
    frames = max(1, round(grid_us * step * FPS / 1e6))
    bpm = FPS * 60 / (frames * row_bar / 4)
    bars = -(-max(s + l for s, l, *_ in notes) // row_bar)
    rows = bars * row_bar

    # tracks: per instrument, a note goes to its first track that is free by then
    tracks = []  # (inst, [notes])
    for n in notes:
        s, l, pitch, inst, vel = n
        for t in tracks:
            if t[0] == inst and t[1][-1][0] + t[1][-1][1] <= s:
                t[1].append(n)
                break
        else:
            tracks.append((inst, [n]))
    tracks.sort(key=lambda t: t[0])  # instruments in project order, their tracks together
    if len(tracks) > 8: print(f"warning: {len(tracks)} tracks, smolt plays 8", file=sys.stderr)

    print(f"# {p['bpm']:g} bpm -> {bpm:g} bpm: {frames} frames per row, {row_bar} rows per bar, {bars} bars")
    print("# tracks 0-3 are never cut off by sfx (@0-3 sfx play over tracks 7-4)")
    for k, inst in enumerate(p["insts"]):
        if any(t[0] == k for t in tracks): print(instrument(k, inst))
    beat = row_bar // 4 if row_bar % 4 == 0 else row_bar

    for ti, (inst, tn) in enumerate(tracks):
        nth = sum(1 for t in tracks[:ti] if t[0] == inst) + 1
        print(f"\n# {p['insts'][inst]['name']} {nth}")
        cells = [""] * rows
        vol = None
        for s, l, pitch, _, vel in tn:
            v = round(vel * 64)
            cells[s] = (f"v{v} " if v != vol else "") + "NOTE%d" % (pitch - 12)
            vol = v
            if s + l < rows and not cells[s + l]: cells[s + l] = "="
        print(f"@{ti} s{frames} i{inst} |")
        lines = []
        for b in range(bars):
            octave, line = None, []
            for r in range(b * row_bar, (b + 1) * row_bar):
                c = cells[r]
                if "NOTE" in c:
                    pre, n = c.split("NOTE")
                    n = int(n)
                    o = n // 12
                    if octave is None: shift = f"o{o}"
                    else: shift = (">" * (o - octave)) + ("<" * (octave - o))
                    octave = o
                    c = pre + shift + NOTE_NAMES[n % 12]
                line.append(c + ",")
                if (r + 1) % beat == 0 and (r + 1) % row_bar: line.append(" ")
            lines.append("".join(line))
        assert sum(l.count(",") for l in lines) == rows   # every track as long as the song, or the loops drift
        # not aligned: the engine reads every character of a song in one frame when it starts
        for b, l in enumerate(lines): print(f"{l}  # {b + 1}")


if __name__ == "__main__":
    main()
