from crane_sorter.instruction import parse_instruction
import pytest


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('青いブロックを右の箱へ', ('blue_cube', 'right')),
        ('赤いブロックを左の箱に入れて', ('red_cube', 'left')),
        ('緑のキューブを右へ', ('green_cube', 'right')),
        ('Put the RED block in the left box', ('red_cube', 'left')),
        ('赤い直方体を右の箱へ', ('red_cuboid', 'right')),
        ('赤い立方体を左の箱へ', ('red_cube', 'left')),
        ('黄色いレンガを左の箱へ', ('yellow_cuboid', 'left')),
        ('put the green brick in the right box', ('green_cuboid', 'right')),
    ],
)
def test_parse_instruction_finds_object_and_box(text: str, expected: tuple[str, str]) -> None:
    assert parse_instruction(text) == expected


@pytest.mark.parametrize(
    'text',
    [
        'ブロックを右の箱へ',          # 物体の色がない
        '赤いのを右の箱へ',            # 物体の形がない
        '青いブロックを箱へ',          # 箱の向きがない
        '赤と青のブロックを右の箱へ',  # 物体の色が 2 つある
        '赤いブロックと直方体を右の箱へ',  # 物体の形が 2 つある
        '青いブロックを右と左の箱へ',  # 箱が 2 つある
    ],
)
def test_parse_instruction_rejects_ambiguous_text(text: str) -> None:
    with pytest.raises(ValueError):
        parse_instruction(text)


@pytest.mark.parametrize(
    ('text', 'reason'),
    [
        ('赤いボールを右の箱へ', '物体の形を表す知っている言葉がありません'),
        ('紫の立方体を左の箱へ', '物体の色を表す知っている言葉がありません'),
        ('赤と青のブロックを右の箱へ', '物体の色を 1 つに決められません'),
    ],
)
def test_parse_instruction_tells_why_it_failed(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_instruction(text)
