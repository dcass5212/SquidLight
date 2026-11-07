"""Audio input: loopback capture of the default output device plus the spectrum analysis.

``AudioAnalyzer`` is pure (blocks in, snapshot out) so it is tested without a sound card.
``AudioWorker`` is the thread that feeds it from WASAPI loopback (via ``soundcard``).
"""

from __future__ import annotations

import logging
import threading
import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
BLOCK = 1024  # about 21 ms: audio-to-light lag stays well under 100 ms
N_BANDS = 16
F_LO, F_HI = 40.0, 16000.0
BASS_HZ = 150.0
_DB_RANGE = 40.0  # a band shows when it is within this many dB of the recent peak
_SILENCE = 1e-4  # amplitude below which the signal counts as silence (-80 dB)
_PEAK_HALF_LIFE_S = 3.0  # slow-decaying peak follower
_RELEASE = 0.8  # per-block fall of displayed values (attack is instant)


def _zeros() -> np.ndarray:
    z = np.zeros(N_BANDS, dtype=np.float32)
    z.setflags(write=False)
    return z


@dataclass(frozen=True)
class AudioSnapshot:
    bands: np.ndarray = field(default_factory=_zeros)  # (16,) 0..1, low to high frequency
    bass: float = 0.0  # 0..1
    level: float = 0.0  # 0..1 overall loudness
    active: bool = False  # capture is running
    error: str = ""  # why it is not, when it is not


class AudioAnalyzer:
    def __init__(self, sample_rate: int = SAMPLE_RATE, block: int = BLOCK) -> None:
        self.sr, self.n = sample_rate, block
        self._window = np.hanning(block).astype(np.float64)
        self._scale = 4.0 / block  # |FFT| of a unit sine under a Hann window is block/4
        freqs = np.fft.rfftfreq(block, 1.0 / sample_rate)
        edges = np.geomspace(F_LO, min(F_HI, sample_rate / 2 * 0.95), N_BANDS + 1)
        lo = np.searchsorted(freqs, edges[:-1])
        hi = np.maximum(np.searchsorted(freqs, edges[1:]), lo + 1)  # at least one bin per band
        self._ranges = list(zip(lo.tolist(), hi.tolist()))
        centers = np.sqrt(edges[:-1] * edges[1:])
        self._tilt_db = 3.0 * np.log2(centers / 500.0)  # music falls ~3 dB/oct; show highs fairly
        self._bass_bins = np.flatnonzero((freqs >= 20.0) & (freqs < BASS_HZ))
        self._peak_db = -120.0
        self._bass_peak = _SILENCE
        self._level_peak = _SILENCE
        self._bands = np.zeros(N_BANDS)
        self._bass = 0.0
        self._level = 0.0
        self._decay = 0.5 ** (block / sample_rate / _PEAK_HALF_LIFE_S)

    def process(self, mono: np.ndarray) -> AudioSnapshot:
        x = np.asarray(mono, dtype=np.float64)
        if len(x) < self.n:
            x = np.pad(x, (0, self.n - len(x)))
        x = x[-self.n :]
        amp = np.abs(np.fft.rfft(x * self._window)) * self._scale
        raw = np.array([amp[a:b].max() for a, b in self._ranges])
        rms = float(np.sqrt(np.mean(x * x)))
        bass_raw = float(np.sqrt(np.mean(amp[self._bass_bins] ** 2))) if len(self._bass_bins) else 0.0

        if raw.max() < _SILENCE:
            target = np.zeros(N_BANDS)
            bass_t = level_t = 0.0
        else:
            db = 20.0 * np.log10(raw + 1e-9) + self._tilt_db
            self._peak_db = max(float(db.max()), self._peak_db - self._fall_db())
            target = np.clip((db - (self._peak_db - _DB_RANGE)) / _DB_RANGE, 0.0, 1.0)
            self._bass_peak = max(bass_raw, self._bass_peak * self._decay, _SILENCE)
            self._level_peak = max(rms, self._level_peak * self._decay, _SILENCE)
            bass_t = min(bass_raw / self._bass_peak, 1.0)
            level_t = min(rms / self._level_peak, 1.0)

        self._bands = np.maximum(target, self._bands * _RELEASE)
        self._bass = max(bass_t, self._bass * _RELEASE)
        self._level = max(level_t, self._level * _RELEASE)
        bands = self._bands.astype(np.float32)
        bands.setflags(write=False)
        return AudioSnapshot(bands, float(self._bass), float(self._level), active=True)

    def _fall_db(self) -> float:
        """How far the peak follower falls per block (6 dB per half-life's worth of decay)."""
        return -20.0 * np.log10(self._decay)


def import_soundcard():
    """Import ``soundcard`` (optional dependency; raises ImportError if missing).

    soundcard calls CoInitializeEx in whichever thread imports it first, and its
    module-level object calls CoUninitialize when it is garbage-collected at interpreter
    exit, on the *main* thread, whose COM belongs to Qt (OLE). We import it on worker
    threads, so that call would be unbalanced: disown it. The worker's COM
    initialization simply lasts until the process ends."""
    import soundcard as sc

    com = getattr(getattr(sc, "mediafoundation", None), "_com", None)
    if com is not None and getattr(com, "com_loaded", False):
        com.com_loaded = False
    return sc


class AudioWorker:
    """Captures the default output device in a thread and publishes the latest snapshot."""

    def __init__(self, device: Optional[str] = None) -> None:
        self._device = device  # speaker name; None follows the Windows default
        self._lock = threading.Lock()
        self._snap = AudioSnapshot(error="starting")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-input", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(2.0)

    def snapshot(self, now: float = 0.0) -> AudioSnapshot:
        with self._lock:
            return self._snap

    def _publish(self, snap: AudioSnapshot) -> None:
        with self._lock:
            self._snap = snap

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                self._capture()
                delay = 1.0
            except Exception as exc:
                log.warning("audio capture failed (%s); retrying in %.0fs", exc, delay)
                self._publish(AudioSnapshot(error=f"Can't capture audio: {exc}"))
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, 15.0)
        self._publish(AudioSnapshot(error="stopped"))

    def _capture(self) -> None:
        sc = import_soundcard()  # imported here: COM is per thread, and the dependency is optional

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "data discontinuity" chatter
            speaker = self._pick(sc)
            mic = sc.get_microphone(speaker.id, include_loopback=True)
            analyzer = AudioAnalyzer()
            log.info("audio: capturing loopback of %s", speaker.name)
            blocks = 0
            with mic.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=BLOCK) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK)
                    self._publish(analyzer.process(np.asarray(data).mean(axis=1)))
                    blocks += 1
                    if self._device is None and blocks % 200 == 0 and sc.default_speaker().id != speaker.id:
                        log.info("audio: default output changed; switching")
                        return

    def _pick(self, sc):
        if self._device:
            for s in sc.all_speakers():
                if s.name == self._device:
                    return s
            log.warning("audio device %r not found; using the default", self._device)
        return sc.default_speaker()


def list_output_devices() -> list[str]:
    """Names of the playback devices, for the Settings picker. Blocking (COM): call it off
    the GUI thread. Empty if the optional ``soundcard`` dependency is missing."""
    try:
        sc = import_soundcard()
    except Exception:
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return [s.name for s in sc.all_speakers()]
    except Exception as exc:
        log.warning("could not list audio devices: %s", exc)
        return []
