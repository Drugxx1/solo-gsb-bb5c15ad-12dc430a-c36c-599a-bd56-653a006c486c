"""摘录包引擎（excerpt pack）。

用户给出若干**起止时间区间**（顺序不限、允许重叠或首尾相接），引擎负责：

1. **合并区间**：将相交或首尾相接（``a.end == b.start``）的输入区间合并为
   互不相连的合并区间，后续一切收录以合并区间为准；
2. **收录转写片段**：只收录**完整落在某个合并区间内部**
   （``seg.start >= r.start`` 且 ``seg.end <= r.end``）的片段；与区间边界
   相交但跨出区间的片段（跨界片段）整条列为**排除项**，绝不把它的全文
   误当成摘录内容；
3. **裁剪校注**：校注与摘录区间相交时按相交部分裁剪边界；一条校注跨越
   多个互不相连的合并区间时拆成多条，每条保留原校注 ID 作为来源标识
   （``sourceAnnotationId``），并取得摘录包内的新 ID
   （``excerpt-<原ID>-1/2/…``，完全内含的单条沿用原 ID）。

整个过程只读，不修改原项目文档。
"""
from __future__ import annotations

from typing import Any

EPS = 1e-6


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def merge_ranges(ranges: list[dict]) -> list[dict]:
    """Merge overlapping or touching (end == start) ranges.

    Input order does not matter. Each input row may carry an optional
    ``note``; merged ranges keep the notes of every row absorbed, in the
    merged time order. Raises ValueError for an empty / invalid row.
    """
    cleaned: list[tuple[float, float, str]] = []
    for raw in ranges or []:
        if not isinstance(raw, dict):
            raise ValueError("时间区间格式不正确")
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None:
            raise ValueError("时间区间必须包含起止时间")
        if start < 0 or end < 0:
            raise ValueError("时间不能为负数")
        if end <= start + EPS:
            raise ValueError("结束时间必须晚于开始时间")
        note = raw.get("note")
        cleaned.append((start, end, str(note) if isinstance(note, str) else ""))
    if not cleaned:
        raise ValueError("请至少填写一个时间区间")

    cleaned.sort(key=lambda x: (x[0], x[1]))
    merged: list[dict] = []
    for start, end, note in cleaned:
        if merged and start <= merged[-1]["end"] + EPS:
            if end > merged[-1]["end"]:
                merged[-1]["end"] = end
            if note and note not in merged[-1]["notes"]:
                merged[-1]["notes"].append(note)
        else:
            merged.append({"start": start, "end": end, "notes": [note] if note else []})
    return merged


def _valid_items(items: list[dict], kind: str) -> list[dict]:
    """Normalize segments / annotations, keep only well-formed ones."""
    out = []
    for order, raw in enumerate(items or []):
        if not isinstance(raw, dict):
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        item = dict(raw)
        item["start"] = start
        item["end"] = end
        item["id"] = str(raw.get("id") or f"{kind}-{order}")
        out.append(item)
    out.sort(key=lambda s: (s["start"], s["end"], s["id"]))
    return out


def _containing_range(seg: dict, ranges: list[dict]) -> dict | None:
    """Return the merged range that fully contains the segment, if any."""
    for rng in ranges:
        if seg["start"] >= rng["start"] - EPS \
                and seg["end"] <= rng["end"] + EPS:
            return rng
    return None


def _overlap(start: float, end: float, rng: dict) -> tuple[float, float] | None:
    lo = max(start, rng["start"])
    hi = min(end, rng["end"])
    if hi - lo > EPS:  # zero-length boundary touches are not an intersection
        return lo, hi
    return None


def _clip_annotations(annotations: list[dict],
                      ranges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Clip annotations to the merged ranges.

    Returns ``(clipped, by_range_index)`` where ``by_range_index[i]`` holds
    the clipped annotation objects contributed to merged range i. An
    annotation spanning several disconnected ranges is split into one copy
    per range, each carrying the original id in ``sourceAnnotationId``.
    """
    clipped: list[dict] = []
    by_range: list[list[dict]] = [[] for _ in ranges]
    for ann in annotations:
        hits: list[tuple[int, float, float]] = []
        for i, rng in enumerate(ranges):
            inter = _overlap(ann["start"], ann["end"], rng)
            if inter is not None:
                hits.append((i, inter[0], inter[1]))
        if not hits:
            continue
        for part_no, (ri, lo, hi) in enumerate(hits, start=1):
            out = dict(ann)
            out["start"] = round(lo, 6)
            out["end"] = round(hi, 6)
            out["sourceAnnotationId"] = ann["id"]
            if len(hits) == 1:
                fully_inside = (
                    abs(ann["start"] - lo) <= EPS and abs(ann["end"] - hi) <= EPS)
                out["id"] = ann["id"] if fully_inside else \
                    f"excerpt-{ann['id']}-1"
                out["clipped"] = not fully_inside
                out["part"] = 1
                out["partCount"] = 1
            else:
                out["id"] = f"excerpt-{ann['id']}-{part_no}"
                out["clipped"] = True
                out["part"] = part_no
                out["partCount"] = len(hits)
            clipped.append(out)
            by_range[ri].append(out)
    clipped.sort(key=lambda a: (a["start"], a["end"], a["id"]))
    for items in by_range:
        items.sort(key=lambda a: (a["start"], a["end"], a["id"]))
    return clipped, by_range


def build_excerpt(document: dict, ranges: list[dict]) -> dict:
    """Build the excerpt-pack preview / export payload for one document.

    Never mutates ``document`` or ``ranges``. Raises ValueError for missing
    or invalid input ranges.
    """
    merged = merge_ranges(ranges)
    document = document or {}
    segments = _valid_items(document.get("segments", []), "seg")
    annotations = _valid_items(document.get("annotations", []), "ann")

    clipped, clipped_by_range = _clip_annotations(annotations, merged)

    # Dedup maps so a segment cannot appear twice even when it crosses the
    # gap between two disconnected merged ranges.
    included_by_id: dict[str, dict] = {}
    excluded_info: dict[str, dict] = {}
    interval_blocks: list[dict] = []
    for i, rng in enumerate(merged):
        included: list[dict] = []
        excluded: list[str] = []
        for seg in segments:
            if _containing_range(seg, [rng]) is not None:
                if seg["id"] not in included_by_id:
                    included_by_id[seg["id"]] = seg
                included.append(seg)
                continue
            if _overlap(seg["start"], seg["end"], rng) is not None:
                info = excluded_info.setdefault(seg["id"], {
                    **seg,
                    "reason": "crosses_interval_boundary",
                    "intervalIndexes": [],
                })
                info["intervalIndexes"].append(i)
                excluded.append(seg["id"])
        interval_blocks.append({
            "index": i,
            "start": rng["start"],
            "end": rng["end"],
            "notes": list(rng["notes"]),
            "segments": included,
            "segmentCount": len(included),
            "annotations": clipped_by_range[i],
            "annotationCount": len(clipped_by_range[i]),
            "excludedSegmentIds": excluded,
            "excludedCount": len(excluded),
        })

    included_segments = [included_by_id[k] for k in sorted(
        included_by_id,
        key=lambda sid: (included_by_id[sid]["start"],
                         included_by_id[sid]["end"], sid))]
    excluded_segments = [excluded_info[k] for k in sorted(
        excluded_info,
        key=lambda sid: (excluded_info[sid]["start"],
                         excluded_info[sid]["end"], sid))]
    return {
        "ranges": merged,
        "inputRangeCount": len(ranges or []),
        "intervals": interval_blocks,
        "segments": included_segments,
        "annotations": clipped,
        "excludedSegments": excluded_segments,
        "summary": {
            "inputRanges": len(ranges or []),
            "mergedRanges": len(merged),
            "segments": len(included_segments),
            "annotations": len(clipped),
            "excludedSegments": len(excluded_segments),
        },
    }
