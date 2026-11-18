"""Royalty-free background music generated from scratch (pad chords, bass, kick, hats, clap).

Used when assets/music has no tracks, so videos are never silent and there are no licence worries.
"""
import wave

import numpy as np

SR = 44100


def _mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _add(buf: np.ndarray, start_s: float, sig: np.ndarray) -> None:
    i = int(start_s * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def _adsr(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    a, r = int(attack * SR), int(release * SR)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.minimum(env[-r:], np.linspace(1, 0, r))
    return env


def make_music(seconds: float, out_path, bpm: int = 108, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    total = seconds + 1.5
    buf = np.zeros(int(total * SR), dtype=np.float32)
    beat = 60.0 / bpm
    bar = beat * 4
    progression = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G

    # pads: two bars per chord
    plen = bar * 2
    pt = np.arange(int(plen * SR)) / SR
    penv = _adsr(len(pt), 0.5, 0.7)
    for ci in range(int(total / plen) + 2):
        for m in progression[ci % 4]:
            f = _mtof(m)
            sig = (np.sin(2 * np.pi * f * pt) + 0.4 * np.sin(2 * np.pi * 2 * f * pt)) * penv * 0.06
            _add(buf, ci * plen, sig.astype(np.float32))

    # bass on every beat
    bt = np.arange(int(0.42 * SR)) / SR
    benv = np.exp(-bt * 6.0)
    for k in range(int(total / beat) + 1):
        root = progression[(k // 8) % 4][0] - 24
        sig = np.sin(2 * np.pi * _mtof(root) * bt) * benv * 0.20
        _add(buf, k * beat, sig.astype(np.float32))

    # kick every beat
    kt = np.arange(int(0.32 * SR)) / SR
    phase = 2 * np.pi * np.cumsum(45 + 80 * np.exp(-kt * 30)) / SR
    kick = (np.sin(phase) * np.exp(-kt * 9) * 0.55).astype(np.float32)
    for k in range(int(total / beat) + 1):
        _add(buf, k * beat, kick)

    # hats on the off-beats, clap on beats 2 and 4
    ht = np.arange(int(0.08 * SR)) / SR
    ct = np.arange(int(0.22 * SR)) / SR
    for k in range(int(total / beat) + 1):
        noise = rng.standard_normal(len(ht)).astype(np.float32)
        hat = np.diff(noise, prepend=0) * np.exp(-ht * 60) * 0.07
        _add(buf, k * beat + beat / 2, hat)
        if k % 4 in (1, 3):
            cn = rng.standard_normal(len(ct)).astype(np.float32)
            _add(buf, k * beat, cn * np.exp(-ct * 24) * 0.10)

    buf = buf[: int(total * SR)]
    n_in, n_out = int(0.5 * SR), int(1.2 * SR)
    buf[:n_in] *= np.linspace(0, 1, n_in)
    buf[-n_out:] *= np.linspace(1, 0, n_out)
    peak = float(np.max(np.abs(buf))) or 1.0
    pcm = (buf / peak * 0.6 * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_calm_music(seconds: float, out_path, seed: int = 11) -> None:
    """Slow, spacious bed for the luxury style: warm pads, a soft sub note, sparse bell notes, echo."""
    rng = np.random.default_rng(seed)
    total = seconds + 2.0
    buf = np.zeros(int(total * SR), dtype=np.float32)
    bar = 60.0 / 64 * 4
    chords = [[57, 60, 64, 67], [53, 57, 60, 64], [48, 52, 55, 59], [55, 59, 62, 64]]  # Am7 Fmaj7 Cmaj7 G6
    clen = bar * 2
    ct = np.arange(int((clen + 2.0) * SR)) / SR
    env = np.minimum(np.minimum(ct / 1.6, 1.0), np.clip((clen + 2.0 - ct) / 2.0, 0, 1)).astype(np.float32)
    for ci in range(int(total / clen) + 2):
        notes = chords[ci % 4]
        for m in notes:
            f = _mtof(m)
            for det in (-0.4, 0.4):
                sig = np.sin(2 * np.pi * (f + det) * ct) + 0.25 * np.sin(2 * np.pi * 2 * f * ct)
                _add(buf, ci * clen, (sig * env * 0.035).astype(np.float32))
        sub = np.sin(2 * np.pi * _mtof(notes[0] - 24) * ct) * env * 0.16
        _add(buf, ci * clen, sub.astype(np.float32))
    # sparse bell notes
    bt = np.arange(int(2.2 * SR)) / SR
    step = 60.0 / 64 * 2
    for k in range(int(total / step)):
        if rng.random() < 0.55:
            ci = int(k * step / clen)
            m = chords[ci % 4][int(rng.integers(0, 4))] + 12
            f = _mtof(m)
            bell = (np.sin(2 * np.pi * f * bt) + 0.3 * np.sin(2 * np.pi * 2.76 * f * bt)) * np.exp(-bt * 2.2) * 0.07
            _add(buf, k * step, bell.astype(np.float32))
    # soft heartbeat on the first beat of each bar
    kt = np.arange(int(0.5 * SR)) / SR
    phase = 2 * np.pi * np.cumsum(50 + 40 * np.exp(-kt * 20)) / SR
    kick = (np.sin(phase) * np.exp(-kt * 7) * 0.22).astype(np.float32)
    for k in range(int(total / bar) + 1):
        _add(buf, k * bar, kick)
    # echo for space
    wet = buf.copy()
    for delay, gain in ((0.43, 0.38), (0.86, 0.24), (1.4, 0.15)):
        d = int(delay * SR)
        wet[d:] += buf[:-d] * gain
    buf = wet[: int(total * SR)]
    n_in, n_out = int(1.2 * SR), int(1.8 * SR)
    buf[:n_in] *= np.linspace(0, 1, n_in)
    buf[-n_out:] *= np.linspace(1, 0, n_out)
    peak = float(np.max(np.abs(buf))) or 1.0
    pcm = (buf / peak * 0.55 * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
