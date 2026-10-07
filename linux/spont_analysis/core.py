"""Pure analysis functions mirroring interactive_pipeline2's spontaneous map."""
from __future__ import annotations

from itertools import combinations
import numpy as np
import pandas as pd

from .burst import detect_burst_intervals


def _first_activation(times, bursts):
    result = np.full(len(bursts), np.nan)
    for i, (b0, b1) in enumerate(bursts):
        lo, hi = np.searchsorted(times, [b0, b1], side="left")
        hi = np.searchsorted(times, b1, side="right")
        if hi > lo: result[i] = (times[lo] - b0) * 1000
    return result


def _peak_ratio(values, bin_ms, signed=False):
    values = np.asarray(values, float); values = values[np.isfinite(values)]
    if values.size < 3: return 0.0, np.nan, 0
    width = max(.1, float(bin_ms))
    if signed:
        limit = max(10, float(np.max(np.abs(values))) + width)
        edges = np.arange(-limit, limit + width * 1.01, width)
    else:
        limit = max(10, float(np.max(values)) + width)
        edges = np.arange(0, limit + width * 1.1, width)
    counts, edges = np.histogram(values, bins=edges)
    idx = int(np.argmax(counts)); nonzero = counts[counts > 0]
    background = float(np.median(nonzero)) if nonzero.size else 1.0
    center = (edges[idx] + edges[idx + 1]) / 2
    return float(counts[idx] / max(background, 1)), float(center), int(counts[idx])


def _select_high_outdegree(electrodes, params):
    candidates = electrodes.copy()
    if params.get("require_stable_activation", False):
        candidates = candidates[candidates["stable_activation"]]
    candidates = candidates[candidates["out_degree"] >= int(params.get("minimum_outdegree", 1))]
    if candidates.empty: return set(), np.nan
    method = str(params.get("method", "percentile"))
    if method == "top_n":
        selected = candidates.nlargest(int(params.get("top_n", 20)), ["out_degree", "out_minus_in"])
        threshold = float(selected["out_degree"].min()) if len(selected) else np.nan
    elif method == "absolute":
        threshold = float(params.get("minimum_outdegree", 1)); selected = candidates[candidates["out_degree"] >= threshold]
    else:
        threshold = float(np.percentile(candidates["out_degree"], float(params.get("percentile", 90))))
        selected = candidates[candidates["out_degree"] >= threshold]
    return set(selected["electrode"].astype(int)), threshold


def analyze_spontaneous_network(session, config, progress=None):
    # Optional observation hook; computation and candidate ordering are unchanged.
    def report(stage, fraction, message, **details):
        if progress:
            progress(stage, fraction, message, **details)
    report('bursts', .12, '检测完整记录中的 Burst')
    spike_lookup = {int(ch): np.sort(session.time_sec[session.channel == ch]) for ch in np.unique(session.channel)}
    burst_cfg = config["burst"]
    bursts, burst_diag = detect_burst_intervals(spike_lookup, **burst_cfg)
    report('activation', .25, '统计每个 Burst 的首次激活', burst_count=len(bursts))
    stable_cfg, pair_cfg = config["stable_activation"], config["directed_connection"]
    first, activation_rows = {}, []
    for index, (ch, times) in enumerate(spike_lookup.items()):
        fa = _first_activation(times, bursts); first[ch] = fa
        ratio, peak, peak_n = _peak_ratio(fa, stable_cfg["histogram_bin_ms"])
        ele = int(session.channel_to_electrode.get(ch, ch))
        activation_rows.append({"channel": ch, "electrode": ele, "bursts_participated": int(np.isfinite(fa).sum()),
            "burst_participation": float(np.isfinite(fa).mean()) if len(fa) else 0,
            "first_activation_peak_ms": peak, "first_activation_peak_count": peak_n,
            "first_activation_peak_ratio": ratio, "stable_activation": ratio >= float(stable_cfg["peak_ratio_threshold"])})
        if index % 20 == 0:
            report('activation', .25 + .10 * (index+1)/len(spike_lookup), '统计首次激活',
                   channels_done=index+1, channels_total=len(spike_lookup))
    edge_rows = []
    total_pairs = len(first)*(len(first)-1)//2
    report('connections', .35, '计算稳定先后关系', pairs_done=0, pairs_total=total_pairs)
    for index, (ca, cb) in enumerate(combinations(sorted(first), 2)):
        if index % max(1, total_pairs//100) == 0:
            report('connections', .35 + .45 * index/max(1, total_pairs), '计算稳定先后关系',
                   pairs_done=index, pairs_total=total_pairs)
        fa, fb = first[ca], first[cb]; both = np.isfinite(fa) & np.isfinite(fb); shared = int(both.sum())
        if shared <= int(pair_cfg["min_shared_bursts"]): continue
        ratio, delay, peak_n = _peak_ratio(fb[both] - fa[both], pair_cfg["delay_bin_ms"], signed=True)
        if ratio < float(pair_cfg["peak_ratio_threshold"]) or not np.isfinite(delay) or abs(delay) <= float(pair_cfg["min_absolute_delay_ms"]): continue
        src, dst = (ca, cb) if delay > 0 else (cb, ca)
        edge_rows.append({"source_channel": src, "target_channel": dst,
            "source_electrode": int(session.channel_to_electrode.get(src, src)), "target_electrode": int(session.channel_to_electrode.get(dst, dst)),
            "shared_bursts": shared, "delay_peak_ms": abs(delay), "signed_cb_minus_ca_ms": delay,
            "delay_peak_ratio": ratio, "delay_peak_count": peak_n})
    report('selection', .80, '汇总出度并筛选候选', pairs_done=total_pairs,
           pairs_total=total_pairs, edge_count=len(edge_rows))
    edges = pd.DataFrame(edge_rows)
    activation = pd.DataFrame(activation_rows)
    duration = max(float(session.time_sec.max() - session.time_sec.min()), 1e-9) if len(session.time_sec) else np.nan
    rows = []
    for _, a in activation.iterrows():
        ch, ele = int(a.channel), int(a.electrode); mask = session.channel == ch
        outgoing = int((edges["source_electrode"] == ele).sum()) if not edges.empty else 0
        incoming = int((edges["target_electrode"] == ele).sum()) if not edges.empty else 0
        xy = session.electrode_positions_um.get(ele, session.channel_positions_um.get(ch, (np.nan, np.nan)))
        rows.append({**a.to_dict(), "x_um": xy[0], "y_um": xy[1], "spike_count": int(mask.sum()),
            "firing_rate_hz": float(mask.sum() / duration), "mean_abs_amplitude": float(np.nanmean(np.abs(session.amplitude[mask]))),
            "out_degree": outgoing, "in_degree": incoming, "out_minus_in": outgoing - incoming})
    electrodes = pd.DataFrame(rows)
    high, threshold = _select_high_outdegree(electrodes, config["high_outdegree"])
    electrodes["high_outdegree"] = electrodes["electrode"].astype(int).isin(high)
    electrodes = electrodes.sort_values(["high_outdegree", "out_degree", "out_minus_in"], ascending=[False, False, False])
    burst_table = pd.DataFrame([{"burst_id": i + 1, "start_sec": a, "end_sec": b, "duration_ms": (b-a)*1000,
                                 "spike_count": int(np.count_nonzero((session.time_sec >= a) & (session.time_sec <= b)))} for i, (a,b) in enumerate(bursts)])
    return {"spike_lookup": spike_lookup, "bursts": bursts, "burst_diagnostics": burst_diag,
            "burst_table": burst_table, "activation": activation, "edges": edges,
            "electrodes": electrodes, "high_outdegree_threshold": threshold}
