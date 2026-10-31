import xml.etree.ElementTree as ElementTree

from crane_sorter.world_models import object_sdf
import pytest


@pytest.mark.parametrize(
    ('shape', 'expected_size'), [('cube', '0.05 0.05 0.05'), ('cuboid', '0.08 0.05 0.05')]
)
def test_object_sdf_sets_box_size_by_shape(shape: str, expected_size: str) -> None:
    model = ElementTree.fromstring(object_sdf('object_red', shape, 0.05, [0.8, 0.0, 0.0]))
    sizes = [element.text for element in model.iter('size')]
    # 見た目と衝突形状の 2 つ
    assert [' '.join(f'{float(value):g}' for value in size.split()) for size in sizes] == [
        expected_size
    ] * 2


def test_object_sdf_makes_standing_cylinder() -> None:
    model = ElementTree.fromstring(object_sdf('object_red', 'cylinder', 0.05, [0.8, 0.0, 0.0]))
    cylinders = list(model.iter('cylinder'))
    # 見た目と衝突形状の 2 つ
    assert len(cylinders) == 2
    for cylinder in cylinders:
        assert float(cylinder.findtext('radius')) == pytest.approx(0.025)
        assert float(cylinder.findtext('length')) == pytest.approx(0.05)


def test_object_sdf_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError):
        object_sdf('object_red', 'sphere', 0.05, [0.8, 0.0, 0.0])
