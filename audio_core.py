from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import parselmouth
from parselmouth.praat import call
from scipy.ndimage import median_filter
from scipy.spatial.distance import cdist

ProgressFn = Callable[[int, str], None]

@dataclass
class PitchTrack:
    times: np.ndarray
    hz: np.ndarray
    midi: np.ndarray
    voiced: np.ndarray
    energy: np.ndarray

@dataclass
class ProcessResult:
    output_path: str
    mean_abs_correction_cents: float
    voiced_frames: int
    duration_seconds: float

def _emit(progress: ProgressFn | None, value: int, text: str) -> None:
    if progress:
        progress(value, text)

def hz_to_midi(hz: np.ndarray) -> np.ndarray:
    hz = np.asarray(hz, dtype=float)
    out = np.full(hz.shape, np.nan, dtype=float)
    mask = hz > 0
    out[mask] = 69.0 + 12.0 * np.log2(hz[mask] / 440.0)
    return out

def midi_to_hz(midi: np.ndarray) -> np.ndarray:
    midi = np.asarray(midi, dtype=float)
    return 440.0 * np.power(2.0, (midi - 69.0) / 12.0)

def _frame_energy(samples: np.ndarray, sr: float, times: np.ndarray, frame_seconds: float = 0.04) -> np.ndarray:
    half = max(1, int(sr * frame_seconds / 2.0))
    energy = np.zeros(len(times), dtype=float)
    for i, t in enumerate(times):
        center = int(round(t * sr))
        a = max(0, center - half)
        b = min(len(samples), center + half)
        if b > a:
            frame = samples[a:b]
            energy[i] = float(np.sqrt(np.mean(frame * frame) + 1e-12))
    p95 = np.percentile(energy, 95) if np.any(energy > 0) else 1.0
    return np.clip(energy / max(p95, 1e-8), 0.0, 1.5)

def extract_pitch(path: str, time_step: float = 0.02) -> tuple[parselmouth.Sound, PitchTrack]:
    sound = parselmouth.Sound(path)
    pitch = sound.to_pitch_ac(time_step=time_step, pitch_floor=55.0, pitch_ceiling=900.0, very_accurate=True)
    hz = pitch.selected_array["frequency"].astype(float)
    times = pitch.xs().astype(float)
    voiced = hz > 0
    midi = hz_to_midi(hz)
    samples = sound.values.mean(axis=0).astype(float)
    energy = _frame_energy(samples, float(sound.sampling_frequency), times)
    return sound, PitchTrack(times=times, hz=hz, midi=midi, voiced=voiced, energy=energy)

def _fill_nan_nearest(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    result = values.copy()
    good = np.isfinite(values)
    if not np.any(good):
        return np.zeros_like(values)
    idx = np.arange(len(values))
    result[~good] = np.interp(idx[~good], idx[good], values[good])
    return result

def _pitch_features(track: PitchTrack) -> np.ndarray:
    midi = _fill_nan_nearest(track.midi)
    angle = 2.0 * np.pi * (np.mod(midi, 12.0) / 12.0)
    voiced = track.voiced.astype(float)
    return np.vstack([np.sin(angle) * voiced, np.cos(angle) * voiced, voiced * 0.75, track.energy * 0.35]).T

def _dtw_path(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        raise ValueError("无法对齐空的音频特征。")
    cost = cdist(x, y, metric="euclidean").astype(np.float32)
    dp = np.full((n, m), np.inf, dtype=np.float32)
    dp[0, 0] = cost[0, 0]
    if n > 1:
        dp[1:, 0] = cost[1:, 0].cumsum() + dp[0, 0]
    if m > 1:
        dp[0, 1:] = cost[0, 1:].cumsum() + dp[0, 0]
    for i in range(1, n):
        row = dp[i]
        prev = dp[i - 1]
        for j in range(1, m):
            row[j] = cost[i, j] + min(prev[j], row[j - 1], prev[j - 1])
    i, j = n - 1, m - 1
    path: list[tuple[int, int]] = [(i, j)]
    while i > 0 or j > 0:
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            candidates = (dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
            step = int(np.argmin(candidates))
            if step == 0:
                i -= 1
            elif step == 1:
                j -= 1
            else:
                i -= 1
                j -= 1
        path.append((i, j))
    path.reverse()
    return np.asarray(path, dtype=np.int32)

def _aligned_reference_midi(ref: PitchTrack, user: PitchTrack, path: np.ndarray) -> np.ndarray:
    aligned = np.full(len(user.midi), np.nan, dtype=float)
    buckets: list[list[float]] = [[] for _ in range(len(user.midi))]
    for ref_i, user_i in path:
        value = ref.midi[ref_i]
        if np.isfinite(value):
            buckets[user_i].append(float(value))
    for i, bucket in enumerate(buckets):
        if bucket:
            aligned[i] = float(np.median(bucket))
    return aligned

def build_target_curve(ref: PitchTrack, user: PitchTrack, strength: float, expression_keep: float) -> tuple[np.ndarray, np.ndarray]:
    path = _dtw_path(_pitch_features(ref), _pitch_features(user))
    aligned_ref = _aligned_reference_midi(ref, user, path)
    user_midi = user.midi.copy()
    user_filled = _fill_nan_nearest(user_midi)
    ref_filled = _fill_nan_nearest(aligned_ref)
    kernel = 9 if len(user_filled) >= 9 else max(1, len(user_filled) // 2 * 2 + 1)
    user_base = median_filter(user_filled, size=kernel, mode="nearest")
    nearest_ref = ref_filled + 12.0 * np.round((user_base - ref_filled) / 12.0)
    target_base = median_filter(nearest_ref, size=kernel, mode="nearest")
    expression = user_filled - user_base
    corrected_base = user_base + np.clip(strength, 0.0, 1.0) * (target_base - user_base)
    target = corrected_base + np.clip(expression_keep, 0.0, 1.0) * expression
    valid = user.voiced & np.isfinite(user_midi) & np.isfinite(aligned_ref)
    target[~valid] = np.nan
    correction_cents = (target - user_midi) * 100.0
    return target, correction_cents

def resynthesize_with_pitch_curve(sound: parselmouth.Sound, times: np.ndarray, target_midi: np.ndarray, output_path: str) -> None:
    manipulation = call(sound, "To Manipulation", 0.01, 55.0, 900.0)
    pitch_tier = call(manipulation, "Extract pitch tier")
    call(pitch_tier, "Remove points between", 0.0, float(sound.duration))
    valid = np.isfinite(target_midi)
    if int(np.sum(valid)) < 3:
        raise ValueError("检测到的有效歌声音高太少，请使用更干净的单人声 WAV。")
    target_hz = midi_to_hz(target_midi[valid])
    valid_times = times[valid]
    for t, hz in zip(valid_times, target_hz):
        if 40.0 <= hz <= 1200.0:
            call(pitch_tier, "Add point", float(t), float(hz))
    call([pitch_tier, manipulation], "Replace pitch tier")
    output = call(manipulation, "Get resynthesis (overlap-add)")
    output.save(output_path, "WAV")

def process_vocals(reference_path: str, user_path: str, output_path: str, strength: float = 0.82, expression_keep: float = 0.72, progress: ProgressFn | None = None) -> ProcessResult:
    reference_path = str(Path(reference_path))
    user_path = str(Path(user_path))
    output_path = str(Path(output_path))
    _emit(progress, 8, "读取参考人声…")
    _, ref = extract_pitch(reference_path)
    _emit(progress, 25, "分析你的音高…")
    user_sound, user = extract_pitch(user_path)
    if np.sum(ref.voiced) < 10:
        raise ValueError("参考人声中没有检测到足够的有效音高。")
    if np.sum(user.voiced) < 10:
        raise ValueError("用户人声中没有检测到足够的有效音高。")
    _emit(progress, 48, "对齐参考唱法与当前演唱…")
    target_midi, correction_cents = build_target_curve(ref, user, strength=strength, expression_keep=expression_keep)
    _emit(progress, 72, "生成自然修音曲线…")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _emit(progress, 82, "PSOLA 重建人声…")
    resynthesize_with_pitch_curve(user_sound, user.times, target_midi, output_path)
    _emit(progress, 100, "完成")
    valid_corr = np.abs(correction_cents[np.isfinite(correction_cents)])
    mean_corr = float(np.mean(valid_corr)) if len(valid_corr) else 0.0
    return ProcessResult(output_path=output_path, mean_abs_correction_cents=mean_corr, voiced_frames=int(np.sum(np.isfinite(target_midi))), duration_seconds=float(user_sound.duration))
