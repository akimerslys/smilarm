"""The OpenCV fallback needs its cascade files; the OpenCV 5 wheels ship an empty cv2/data folder."""
import pytest

from smilarm import smile


def test_find_cascades_picks_the_first_complete_folder(tmp_path):
    empty, half, full = tmp_path / "empty", tmp_path / "half", tmp_path / "full"
    for d in (empty, half, full):
        d.mkdir()
    (half / smile.CASCADES[0]).write_text("x")
    for name in smile.CASCADES:
        (full / name).write_text("x")
    assert smile.find_cascades([str(tmp_path / "nope"), str(empty), str(half), str(full)]) == full
    assert smile.find_cascades([str(empty), str(half), ""]) is None  # one file is not enough


def test_missing_cascades_fail_loudly_instead_of_building_a_blind_detector(tmp_path):
    with pytest.raises(RuntimeError, match="cascade files"):
        smile.HaarDetector(dirs=[str(tmp_path)])


def test_make_detector_surfaces_the_error_so_the_engine_can_degrade(tmp_path, monkeypatch):
    monkeypatch.setattr(smile, "cascade_dirs", lambda: [str(tmp_path)])
    with pytest.raises(RuntimeError):
        smile.make_detector("haar", str(tmp_path / "no-model.task"))


def test_search_path_includes_the_image_and_installer_locations(monkeypatch):
    monkeypatch.setenv("SMILARM_CASCADES", "/somewhere")
    dirs = smile.cascade_dirs()
    assert "/somewhere" in dirs and "data/cascades" in dirs and "/opt/models" in dirs


def test_real_cascades_load_when_opencv_has_them():
    cv2 = pytest.importorskip("cv2")
    import os

    if not smile.find_cascades([getattr(getattr(cv2, "data", None), "haarcascades", "")]):
        pytest.skip("this OpenCV build ships no cascade files (OpenCV 5): covered by the Docker image/installer copies")
    import numpy as np

    d = smile.HaarDetector()
    assert d.score(np.zeros((240, 320, 3), np.uint8)) is None  # blank frame: no face
