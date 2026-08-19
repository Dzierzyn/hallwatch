"""Pipeline pieces that need no camera: active-hours windows, event kinds."""

from __future__ import annotations

import time

from hallwatch.config import CameraProfile
from hallwatch.pipeline import Pipeline


class _Stub:
    """Just enough of a Pipeline to call its pure-logic methods."""

    _in_active_window = Pipeline._in_active_window
    _event_kind = Pipeline._event_kind

    def __init__(self, **prof_kwargs):
        self.prof = CameraProfile(name="t", **prof_kwargs)
        self._event_max_persons = 0
        self._event_peak_dbfs = None
        self._event_classes = __import__("collections").Counter()


def at_hour(hour: int) -> float:
    lt = list(time.localtime())
    lt[3], lt[4], lt[5] = hour, 0, 0
    return time.mktime(tuple(lt))


def test_active_hours_daytime_window():
    s = _Stub(active_hours="07:00-23:00")
    assert s._in_active_window(at_hour(12))
    assert not s._in_active_window(at_hour(3))
    assert not s._in_active_window(at_hour(23))  # end is exclusive


def test_active_hours_over_midnight():
    s = _Stub(active_hours="22:00-06:00")
    assert s._in_active_window(at_hour(23))
    assert s._in_active_window(at_hour(2))
    assert not s._in_active_window(at_hour(12))


def test_active_hours_none_and_garbage_never_block():
    assert _Stub(active_hours=None)._in_active_window(at_hour(3))
    assert _Stub(active_hours="garbage")._in_active_window(at_hour(3))


def test_event_kind_from_detected_classes():
    s = _Stub()
    s._event_max_persons = 2
    s._event_classes.update({"car": 30, "truck": 4})
    assert s._event_kind() == "vehicle"

    s2 = _Stub()
    s2._event_max_persons = 1
    s2._event_classes.update({"person": 12})
    assert s2._event_kind() == "person"


def test_event_kind_motion_and_audio_fallbacks():
    s = _Stub()
    assert s._event_kind() == "motion"
    s._event_peak_dbfs = -30.0
    assert s._event_kind() == "audio"
