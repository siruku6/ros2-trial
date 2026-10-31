"""文の指示から、掴む物体の名前と置く箱の名前を取り出す。

決まった言葉を探すだけの解釈である。物体の名前は「色_形」で、layout.yaml の label に合わせる。
箱の名前は boxes の name に合わせる。
"""

_COLOR_WORDS: dict[str, tuple[str, ...]] = {
    'red': ('赤', 'red'),
    'green': ('緑', 'green'),
    'blue': ('青', 'blue'),
    'yellow': ('黄', 'yellow'),
}
# 「四角」は「四角い箱」と区別できないので入れない
_SHAPE_WORDS: dict[str, tuple[str, ...]] = {
    'cube': ('ブロック', 'キューブ', '立方体', 'block', 'cube'),
    'cuboid': ('直方体', 'レンガ', 'cuboid', 'brick'),
}
_BOX_WORDS: dict[str, tuple[str, ...]] = {
    'right': ('右', 'right'),
    'left': ('左', 'left'),
}


def parse_instruction(text: str) -> tuple[str, str]:
    """「青いブロックを右の箱へ」のような指示から、物体の名前と箱の名前を取り出す。

    Parameters
    ----------
    text : str
        日本語か英語の指示。例: "青いブロックを右の箱へ"、"put the blue block in the right box"

    Returns
    -------
    object_label : str
        掴む物体の名前。色と形を _ でつなぐ。例: "blue_cube"
    box_name : str
        置く箱の名前。例: "right"

    Raises
    ------
    ValueError
        物体の色か形、または箱が 1 つに決まらないとき（見つからない、または 2 つ以上あるとき）。
    """
    lowered = text.lower()
    color = _find_exactly_one(lowered, _COLOR_WORDS, '物体の色')
    shape = _find_exactly_one(lowered, _SHAPE_WORDS, '物体の形')
    box_name = _find_exactly_one(lowered, _BOX_WORDS, '箱')
    return f'{color}_{shape}', box_name


def _find_exactly_one(text: str, vocabulary: dict[str, tuple[str, ...]], kind: str) -> str:
    """文に含まれる言葉から、名前をちょうど 1 つ選ぶ。"""
    found = [name for name, words in vocabulary.items() if any(word in text for word in words)]
    if len(found) != 1:
        raise ValueError(f'指示から{kind}を 1 つに決められません（候補: {found}）: {text}')
    return found[0]
