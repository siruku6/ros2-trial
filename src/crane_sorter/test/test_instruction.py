from crane_sorter.instruction import parse_instruction
import pytest


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('青いブロックを右の箱へ', ('blue', 'right')),
        ('赤いブロックを左の箱に入れて', ('red', 'left')),
        ('緑のキューブを右へ', ('green', 'right')),
        ('Put the RED block in the left box', ('red', 'left')),
    ],
)
def test_parse_instruction_finds_object_and_box(text: str, expected: tuple[str, str]) -> None:
    assert parse_instruction(text) == expected


@pytest.mark.parametrize(
    'text',
    [
        'ブロックを右の箱へ',          # 物体の色がない
        '青いブロックを箱へ',          # 箱の向きがない
        '赤と青のブロックを右の箱へ',  # 物体が 2 つある
        '青いブロックを右と左の箱へ',  # 箱が 2 つある
    ],
)
def test_parse_instruction_rejects_ambiguous_text(text: str) -> None:
    with pytest.raises(ValueError):
        parse_instruction(text)
