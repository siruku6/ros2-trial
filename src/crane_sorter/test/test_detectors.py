from crane_sorter.detectors import assign_labels, Candidate, to_detections

_LABELS = ['red_cube', 'red_cuboid']


def test_assign_labels_uses_relative_scores_of_similar_objects() -> None:
    # どちらの枠も red_cuboid の点数のほうが高いが、1 対 1 に組み合わせると、
    # red_cube らしさが相対的に高い左の枠が red_cube になる
    left = Candidate((0, 0, 10, 10), (0.6, 0.7))
    right = Candidate((20, 0, 30, 10), (0.4, 0.8))
    assert assign_labels([left, right], _LABELS, 0.1) == {'red_cube': 0, 'red_cuboid': 1}


def test_assign_labels_skips_scores_below_threshold() -> None:
    only = Candidate((0, 0, 10, 10), (0.05, 0.9))
    assert assign_labels([only], _LABELS, 0.1) == {'red_cuboid': 0}


def test_assign_labels_with_no_candidates() -> None:
    assert assign_labels([], _LABELS, 0.1) == {}


def test_to_detections_uses_score_of_assigned_label() -> None:
    candidates = [Candidate((10, 20, 30, 60), (0.9, 0.2))]
    detection = to_detections(candidates, _LABELS, {'red_cuboid': 0})[0]
    assert (detection.label, detection.score) == ('red_cuboid', 0.2)
    assert detection.center == (20.0, 40.0)
