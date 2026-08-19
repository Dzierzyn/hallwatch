"""Line-crossing logic: the geometry that makes counting trustworthy."""

from __future__ import annotations

from hallwatch.config import CountingCfg
from hallwatch.counter import PeopleCounter
from hallwatch.detect import Detection

SHAPE = (400, 600)  # h, w
LINE = ((0.0, 0.5), (1.0, 0.5))  # horizontal line at y=200px


def det(track_id: int, cx: float, feet_y: float) -> Detection:
    """A detection whose feet anchor lands at (cx, feet_y) in pixels."""
    w, h = 40.0, 100.0
    return Detection(
        track_id=track_id,
        cls=0,
        conf=0.9,
        xyxy=(cx - w / 2, feet_y - h, cx + w / 2, feet_y),
    )


def test_single_crossing_counted_once():
    c = PeopleCounter(CountingCfg(line=LINE))
    crossings = []
    # walk one track from above the line to below it across several frames
    for i, y in enumerate([120, 160, 190, 210, 240, 280]):
        crossings += c.update([det(1, 300, y)], SHAPE, ts=float(i))
    assert len(crossings) == 1
    assert c.state.positive + c.state.negative == 1


def test_no_count_when_walking_parallel_to_line():
    c = PeopleCounter(CountingCfg(line=LINE))
    crossings = []
    for i, x in enumerate(range(50, 550, 50)):
        crossings += c.update([det(1, float(x), 150.0)], SHAPE, ts=float(i))
    assert crossings == []


def test_no_count_beyond_segment_end():
    # line only spans x in [0.4, 0.6] of the frame; someone crossing at x=50px
    # crosses the infinite LINE but not the SEGMENT and must not count
    c = PeopleCounter(CountingCfg(line=((0.4, 0.5), (0.6, 0.5))))
    crossings = []
    for i, y in enumerate([150, 190, 220, 260]):
        crossings += c.update([det(1, 50.0, y)], SHAPE, ts=float(i))
    assert crossings == []


def test_direction_labels_follow_sign():
    cfg = CountingCfg(
        line=LINE,
        direction_labels={"positive": "in", "negative": "out"},
    )
    c = PeopleCounter(cfg)
    down = []
    for i, y in enumerate([150, 250]):
        down += c.update([det(1, 300, y)], SHAPE, ts=float(i))
    up = []
    for i, y in enumerate([250, 150]):
        up += c.update([det(2, 300, y)], SHAPE, ts=float(10 + i))
    assert {d.direction for d in down} != {u.direction for u in up}
    assert c.state.positive == 1 and c.state.negative == 1


def test_two_tracks_counted_independently():
    c = PeopleCounter(CountingCfg(line=LINE))
    crossings = []
    for i, y in enumerate([150, 250]):
        crossings += c.update([det(1, 200, y), det(2, 400, y)], SHAPE, ts=float(i))
    assert len(crossings) == 2
    assert c.state.unique_seen == 2


def test_zone_presence():
    cfg = CountingCfg(
        zones=[{"name": "doorway", "polygon": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]
    )
    c = PeopleCounter(cfg)
    c.update([det(1, 100, 200), det(2, 500, 200)], SHAPE, ts=0.0)
    assert c.state.zone_counts["doorway"] == 1  # only the left-half track


def test_stale_tracks_garbage_collected():
    c = PeopleCounter(CountingCfg(line=LINE))
    c.update([det(1, 300, 150)], SHAPE, ts=0.0)
    assert 1 in c.trails
    c.update([], SHAPE, ts=100.0)  # 100s later, well past max_idle
    assert 1 not in c.trails
