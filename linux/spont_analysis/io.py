"""Read MaxWell spontaneous H5 spikes/mapping without loading raw waveforms."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np

CFG_RE = re.compile(r"(\d+)\((\d+)\)([\d.+-]+)/([\d.+-]+)")


@dataclass
class Session:
    path: Path
    sampling_hz: float
    frame_origin: int
    frameno: np.ndarray
    channel: np.ndarray
    amplitude: np.ndarray
    time_sec: np.ndarray
    channel_to_electrode: dict[int, int]
    channel_positions_um: dict[int, tuple[float, float]]
    electrode_positions_um: dict[int, tuple[float, float]]


def _segment(f, well=0):
    key = f'wells/well{well:03d}/rec0000'
    if key in f:
        return f[key]
    if "data_store" in f:
        keys = sorted(k for k in f["data_store"] if k.startswith("data"))
        if keys: return f["data_store"][keys[0]]
    if "recordings" in f:
        keys = sorted(k for k in f["recordings"] if k.startswith("rec"))
        if keys:
            rec = f["recordings"][keys[0]]
            return rec[f'well{well:03d}'] if f'well{well:03d}' in rec else rec
    raise KeyError("H5中未找到data_store/dataXXXX或recordings/recXXXX")


def _first_raw_frame(seg):
    values = []
    if "groups" in seg:
        for group in seg["groups"].values():
            if "frame_nos" in group and group["frame_nos"].size:
                values.append(int(group["frame_nos"][0]))
    return min(values) if values else None


def _read_mapping(seg):
    mapping = None
    for path in ("settings/mapping", "mapping"):
        try: mapping = seg[path][:]; break
        except KeyError: pass
    ch_to_ele, ch_pos = {}, {}
    if mapping is None or not mapping.dtype.names or "channel" not in mapping.dtype.names:
        return ch_to_ele, ch_pos
    names = mapping.dtype.names
    for row in mapping:
        ch = int(row["channel"]); ele = int(row["electrode"]) if "electrode" in names else ch
        ch_to_ele[ch] = ele
        if "x" in names and "y" in names:
            x, y = float(row["x"]), float(row["y"])
            if np.isfinite(x) and np.isfinite(y): ch_pos[ch] = (x, y)
    return ch_to_ele, ch_pos


def parse_cfg_positions(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text[:text.find("H4sI")] if "H4sI" in text else text
    positions = {}
    for match in CFG_RE.finditer(text):
        ch, ele = int(match.group(1)), int(match.group(2))
        xy = (float(match.group(3)), float(match.group(4)))
        positions[ch] = xy; positions[ele] = xy
    return positions


def read_session(h5_path: Path, cfg_path: Path | None = None, well=0):
    h5_path = Path(h5_path).resolve()
    with h5py.File(h5_path, "r") as f:
        seg = _segment(f, well)
        spikes = seg["spikes"]
        names = spikes.dtype.names or ()
        if "frameno" not in names or "channel" not in names:
            raise KeyError("spikes数据集缺少frameno/channel字段")
        frameno = np.asarray(spikes["frameno"], np.int64)
        channel = np.asarray(spikes["channel"], np.int32)
        amplitude = np.asarray(spikes["amplitude"], np.float32) if "amplitude" in names else np.full(len(frameno), np.nan, np.float32)
        sampling = 20000.0
        try: sampling = float(np.asarray(seg["settings/sampling"][()]).reshape(-1)[0])
        except (KeyError, TypeError, ValueError): pass
        origin = _first_raw_frame(seg)
        if origin is None: origin = int(frameno.min()) if len(frameno) else 0
        ch_to_ele, ch_pos = _read_mapping(seg)
    ele_pos = {ch_to_ele.get(ch, ch): xy for ch, xy in ch_pos.items()}
    if cfg_path:
        cfg_pos = parse_cfg_positions(Path(cfg_path))
        for ch, ele in ch_to_ele.items():
            xy = cfg_pos.get(ele, cfg_pos.get(ch))
            if xy is not None: ch_pos[ch] = xy; ele_pos[ele] = xy
    return Session(h5_path, sampling, origin, frameno, channel, amplitude,
                   (frameno.astype(float) - origin) / max(sampling, 1), ch_to_ele, ch_pos, ele_pos)
