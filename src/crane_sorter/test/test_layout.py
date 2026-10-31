import math
from pathlib import Path
import random

from crane_sorter.layout import box_placements, load_layout, sample_object_placements
import pytest

_CONFIG_DIR = Path(__file__).resolve().parents[1] / 'config'


@pytest.fixture(scope='module', params=['layout.yaml', 'layout_shapes.yaml'])
def layout(request: pytest.FixtureRequest) -> dict:
    return load_layout(_CONFIG_DIR / request.param)


@pytest.mark.parametrize('seed', range(200))
def test_sampled_objects_satisfy_spawn_area(layout: dict, seed: int) -> None:
    area = layout['spawn_area']
    placements = sample_object_placements(layout, random.Random(seed))

    assert [placement.label for placement in placements] == [
        spec['label'] for spec in layout['objects']
    ]
    for placement in placements:
        radius = math.hypot(placement.x, placement.y)
        angle = math.degrees(math.atan2(placement.y, placement.x))
        assert area['min_radius'] <= radius <= area['max_radius']
        assert area['min_angle_deg'] <= angle <= area['max_angle_deg']
        assert placement.yaw == pytest.approx(math.radians(angle))
        for box in box_placements(layout):
            assert math.hypot(placement.x - box.x, placement.y - box.y) >= area['min_box_distance']
    for index, first in enumerate(placements):
        for second in placements[index + 1:]:
            assert math.hypot(first.x - second.x, first.y - second.y) >= area['min_separation']


def test_same_seed_gives_same_layout(layout: dict) -> None:
    first = sample_object_placements(layout, random.Random(42))
    second = sample_object_placements(layout, random.Random(42))
    assert first == second
