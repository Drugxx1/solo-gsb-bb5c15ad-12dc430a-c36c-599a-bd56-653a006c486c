"""Engine + end-to-end API tests for excerpt packs (摘录包).

Run with: python3 test_excerpts_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="excerpt-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.excerpts import build_excerpt, merge_ranges  # noqa: E402

# Timeline (seconds):
#  s1 [0,5]   s2 [5,10] (touches s1)   s3 [10,20]   s4 [20,30]
#  a fully-inside annotation ann-in [2,4]
#  a boundary-crossing annotation ann-cross [8,22] -> clipped/split
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5, "speaker": "甲", "text": "第一段"},
        {"id": "s2", "start": 5, "end": 10, "speaker": "甲", "text": "第二段"},
        {"id": "s3", "start": 10, "end": 20, "speaker": "乙", "text": "第三段"},
        {"id": "s4", "start": 20, "end": 30, "speaker": "乙", "text": "第四段"},
    ],
    "annotations": [
        {"id": "ann-in", "start": 2, "end": 4, "speaker": "甲",
         "topic": "内含"},
        {"id": "ann-cross", "start": 8, "end": 22, "speaker": "乙",
         "topic": "跨界", "note": "原备注"},
        {"id": "ann-out", "start": 40, "end": 50, "speaker": "乙",
         "topic": "无关"},
    ],
}


def test_merge_ranges():
    # overlapping, out of order, touching
    out = merge_ranges([
        {"start": 10, "end": 20},
        {"start": 0, "end": 5},
        {"start": 5, "end": 12},
        {"start": 30, "end": 40},
    ])
    assert [(r["start"], r["end"]) for r in out] == [(0, 20), (30, 40)]

    # notes attached to input rows are kept on the merged range
    out = merge_ranges([
        {"start": 0, "end": 2, "note": "a"},
        {"start": 1, "end": 3, "note": "b"},
        {"start": 9, "end": 10, "note": "a"},
    ])
    assert out[0]["notes"] == ["a", "b"]
    assert out[1]["notes"] == ["a"]

    for bad in ([], [{"start": 5, "end": 5}], [{"start": 6, "end": 5}],
                [{"start": -1, "end": 5}], [{"start": "x", "end": 5}]):
        try:
            merge_ranges(bad)
            raise AssertionError(f"expected ValueError: {bad}")
        except ValueError:
            pass


def test_build_excerpt():
    # Two touching input ranges merge into [0,10]; s1,s2 are fully inside.
    # s3 [10,20] only touches the merged range end (zero overlap under the
    # half-open interval convention), so it is neither included nor excluded.
    pack = build_excerpt(DOC, [{"start": 0, "end": 8}, {"start": 8, "end": 10}])
    assert pack["summary"]["inputRanges"] == 2
    assert pack["summary"]["mergedRanges"] == 1
    rng = pack["intervals"][0]
    assert (rng["start"], rng["end"]) == (0, 10)
    assert [s["id"] for s in rng["segments"]] == ["s1", "s2"]
    assert [s["id"] for s in pack["segments"]] == ["s1", "s2"]
    assert rng["excludedSegmentIds"] == []
    assert pack["excludedSegments"] == []
    # ann-in is fully inside: id kept, not clipped
    by_id = {a["id"]: a for a in rng["annotations"]}
    assert "ann-in" in by_id
    assert by_id["ann-in"]["clipped"] is False
    assert by_id["ann-in"]["sourceAnnotationId"] == "ann-in"
    # ann-cross intersects at [8,10]: clipped, new id, source preserved
    clipped = [a for a in rng["annotations"]
               if a["sourceAnnotationId"] == "ann-cross"]
    assert len(clipped) == 1
    assert clipped[0]["id"] == "excerpt-ann-cross-1"
    assert (clipped[0]["start"], clipped[0]["end"]) == (8, 10)
    assert clipped[0]["clipped"] is True
    assert clipped[0]["note"] == "原备注"  # other fields preserved
    # ann-out never appears
    assert all(a["sourceAnnotationId"] != "ann-out"
               for a in pack["annotations"])


def test_split_annotation_across_disconnected_ranges():
    # [0,6) and [10,20) are disconnected: s1 fully inside the first,
    # s3 fully inside the second. s2 crosses range0's end; an extra long
    # segment sgap crosses both ranges (deduplicated once at top level).
    # ann-span [2,28] meets both ranges and must be split into two parts.
    doc = {
        "segments": DOC["segments"] + [
            {"id": "sgap", "start": 4, "end": 26, "speaker": "丙",
             "text": "长片段"}],
        "annotations": DOC["annotations"] + [
            {"id": "ann-span", "start": 2, "end": 28, "topic": "跨越两段"}],
    }
    pack = build_excerpt(doc, [{"start": 10, "end": 20}, {"start": 0, "end": 6}])
    assert pack["summary"]["mergedRanges"] == 2
    i0, i1 = pack["intervals"]  # merged ranges sorted by start
    assert (i0["start"], i0["end"]) == (0, 6)
    assert (i1["start"], i1["end"]) == (10, 20)
    assert [s["id"] for s in i0["segments"]] == ["s1"]
    assert [s["id"] for s in i1["segments"]] == ["s3"]
    assert i0["excludedSegmentIds"] == ["sgap", "s2"]
    assert i1["excludedSegmentIds"] == ["sgap"]

    # the long crossing segment is listed once with both interval indexes
    excluded = {s["id"]: s for s in pack["excludedSegments"]}
    assert sorted(excluded) == ["s2", "sgap"]
    # top-level exclusion list is time-sorted (sgap starts at 4, s2 at 5)
    assert [s["id"] for s in pack["excludedSegments"]] == ["sgap", "s2"]
    assert excluded["s2"]["intervalIndexes"] == [0]
    assert excluded["sgap"]["intervalIndexes"] == [0, 1]

    parts = [a for a in pack["annotations"]
             if a["sourceAnnotationId"] == "ann-span"]
    assert len(parts) == 2
    assert [(p["start"], p["end"]) for p in parts] == [(2, 6), (10, 20)]
    assert [p["id"] for p in parts] == [
        "excerpt-ann-span-1", "excerpt-ann-span-2"]
    assert all(p["clipped"] for p in parts)
    assert [p["part"] for p in parts] == [1, 2]
    assert all(p["partCount"] == 2 for p in parts)
    # each part lives in exactly one interval block (alongside local anns)
    assert [a["id"] for a in i0["annotations"]] == [
        "ann-in", "excerpt-ann-span-1"]
    assert [a["id"] for a in i1["annotations"]] == [
        "excerpt-ann-cross-1", "excerpt-ann-span-2"]
    # ann-cross [8,22] single-hit on range1: clipped to [10,20]
    cross = next(a for a in i1["annotations"]
                 if a["sourceAnnotationId"] == "ann-cross")
    assert (cross["start"], cross["end"]) == (10, 20)


def test_overlapping_input_ranges_and_boundary_touch():
    # fully-overlapping duplicates collapse to one merged range; a segment
    # exactly ending at the range boundary is still "fully inside".
    pack = build_excerpt(DOC, [
        {"start": 0, "end": 30}, {"start": 0, "end": 30}])
    assert pack["summary"]["mergedRanges"] == 1
    assert pack["summary"]["segments"] == 4
    assert pack["summary"]["excludedSegments"] == 0
    # ann-cross [8,22] fully inside [0,30]: id kept
    ids = {a["id"] for a in pack["annotations"]}
    assert "ann-cross" in ids


def test_no_mutation():
    import copy
    before = copy.deepcopy(DOC)
    ranges = [{"start": 0, "end": 10, "note": "n"}]
    build_excerpt(DOC, ranges)
    assert DOC == before
    assert ranges == [{"start": 0, "end": 10, "note": "n"}]


def test_api():
    with TestClient(app) as c:
        p = c.post("/api/projects", json={"name": "摘录包访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "摘录包访谈", "document": DOC})

        body = {"ranges": [
            {"start": 8, "end": 22, "note": "核心"},
            {"start": 0, "end": 5}]}
        r = c.post(f"/api/projects/{pid}/excerpt-pack/preview", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "摘录包访谈"
        assert "createdAt" in data
        assert data["summary"]["inputRanges"] == 2
        # [0,5) and [8,22) stay disconnected
        assert [(x["start"], x["end"]) for x in data["ranges"]] == \
            [(0, 5), (8, 22)]
        assert data["ranges"][1]["notes"] == ["核心"]
        # s1 fully in first; nothing fully inside [8,22) (s2 ends at 10 but
        # starts before 8 -> excluded; s3 starts at 10, ends at 20 -> inside)
        i0, i1 = data["intervals"]
        assert [s["id"] for s in i0["segments"]] == ["s1"]
        assert [s["id"] for s in i1["segments"]] == ["s3"]
        assert i1["excludedSegmentIds"] == ["s2", "s4"]
        # ann-cross [8,22] fully inside the [8,22) boundary itself
        cross = [a for a in i1["annotations"]
                 if a["sourceAnnotationId"] == "ann-cross"]
        assert len(cross) == 1 and cross[0]["id"] == "ann-cross"

        # invalid range -> 400
        assert c.post(f"/api/projects/{pid}/excerpt-pack/preview",
                      json={"ranges": []}).status_code == 400
        assert c.post(f"/api/projects/{pid}/excerpt-pack/preview",
                      json={"ranges": [{"start": 9, "end": 9}]}).status_code == 400

        # download endpoint returns an attachment JSON with the same payload
        r = c.post(f"/api/projects/{pid}/excerpt-pack/download", json=body)
        assert r.status_code == 200, r.text
        cd = r.headers["content-disposition"]
        assert "attachment" in cd and "excerpt-pack.json" in cd
        dl = r.json()
        assert dl["summary"] == data["summary"]
        assert dl["segments"] and dl["annotations"] and dl["excludedSegments"]
        # interval blocks carry counts
        for it in dl["intervals"]:
            assert it["segmentCount"] == len(it["segments"])
            assert it["annotationCount"] == len(it["annotations"])
            assert it["excludedCount"] == len(it["excludedSegmentIds"])

        # the saved project document is unchanged after preview + download
        again = c.get(f"/api/projects/{pid}").json()
        assert again["document"] == DOC

        # request-carried document works (unsaved editor state)
        dirty = {"segments": [
            {"id": "n1", "start": 0, "end": 2, "speaker": "X",
             "text": "未保存"}], "annotations": []}
        r = c.post(f"/api/projects/{pid}/excerpt-pack/preview",
                   json={"ranges": [{"start": 0, "end": 2}],
                         "document": dirty})
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["segments"]] == ["n1"]

        # unknown project -> 404
        assert c.post("/api/projects/nope/excerpt-pack/preview",
                      json={"ranges": [{"start": 0, "end": 1}]}).status_code == 404

        print("ALL EXCERPT PACK API TESTS PASSED")


def run():
    test_merge_ranges()
    test_build_excerpt()
    test_split_annotation_across_disconnected_ranges()
    test_overlapping_input_ranges_and_boundary_touch()
    test_no_mutation()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
