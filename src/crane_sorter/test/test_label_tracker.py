from crane_sorter.detectors import Candidate
from crane_sorter.label_tracker import LabelTracker, Observation

_LABELS = ['red_cube', 'red_cuboid']


def _observation(x: float, y: float, cube_score: float, cuboid_score: float) -> Observation:
    return Observation(Candidate((0, 0, 1, 1), (cube_score, cuboid_score)), x, y)


def test_keeps_labels_when_appearance_changes_after_one_object_moves() -> None:
    tracker = LabelTracker(_LABELS, score_threshold=0.1)
    cube_on_table = _observation(0.20, 0.00, 0.5, 0.6)
    cuboid_on_table = _observation(0.20, 0.10, 0.3, 0.7)
    assert tracker.update([cube_on_table, cuboid_on_table], now=0.0) == {
        'red_cube': 0, 'red_cuboid': 1,
    }

    # 直方体が箱に運ばれ、箱の中ではキューブらしく見えるようになった
    cuboid_in_box = _observation(0.04, 0.23, 0.6, 0.3)
    assert tracker.update([cube_on_table, cuboid_in_box], now=10.0) == {
        'red_cube': 0, 'red_cuboid': 1,
    }


def test_briefly_hidden_label_is_not_given_to_another_candidate() -> None:
    tracker = LabelTracker(_LABELS, score_threshold=0.1, release_seconds=2.0)
    tracker.update([_observation(0.20, 0.00, 0.6, 0.3), _observation(0.20, 0.10, 0.3, 0.6)], 0.0)

    # キューブが腕に隠れ、運ばれている直方体がキューブらしく見えても、キューブの名前は付けない
    carried_cuboid = _observation(0.10, 0.10, 0.7, 0.2)
    assert tracker.update([carried_cuboid], now=1.0) == {}


def test_label_unseen_for_long_is_given_to_new_candidate() -> None:
    tracker = LabelTracker(_LABELS, score_threshold=0.1, release_seconds=2.0)
    tracker.update([_observation(0.20, 0.00, 0.6, 0.3), _observation(0.20, 0.10, 0.3, 0.6)], 0.0)

    cube = _observation(0.20, 0.00, 0.6, 0.3)
    cuboid_in_box = _observation(0.04, 0.23, 0.2, 0.3)
    assert tracker.update([cube, cuboid_in_box], now=5.0) == {'red_cube': 0, 'red_cuboid': 1}


def test_does_not_keep_label_for_object_of_other_color_at_same_place() -> None:
    tracker = LabelTracker(_LABELS, score_threshold=0.1)
    tracker.update([_observation(0.04, 0.23, 0.3, 0.6)], 0.0)

    # 同じ場所に、どちらの名前の点数も低い物体（例: 上に積まれた青いキューブ）が見える
    assert tracker.update([_observation(0.04, 0.23, 0.02, 0.03)], 1.0) == {}
