"""仕分けの環境の配置（箱の位置と、物体を置く位置）を決める。"""

from dataclasses import dataclass
import math
from pathlib import Path
import random

import yaml


@dataclass(frozen=True)
class BoxPlacement:
    """箱 1 個の名前と、crane_plus_base から見た底面の中心の位置 [m]。"""

    name: str
    x: float
    y: float


@dataclass(frozen=True)
class ObjectPlacement:
    """物体 1 個の名前と、crane_plus_base から見た位置 [m] と向き [rad]。"""

    label: str
    x: float
    y: float
    yaw: float


def load_layout(layout_path: Path) -> dict:
    """配置の設定ファイルを読む。

    Reads:
      - config/layout.yaml（または引数で渡したファイル）
    """
    with open(layout_path, encoding='utf-8') as layout_file:
        return yaml.safe_load(layout_file)


def box_placements(layout: dict) -> list[BoxPlacement]:
    """設定に書かれた箱の位置を返す。"""
    return [BoxPlacement(box['name'], float(box['x']), float(box['y'])) for box in layout['boxes']]


def sample_object_placements(
    layout: dict, rng: random.Random, max_attempts: int = 1000
) -> list[ObjectPlacement]:
    """物体を置く位置を、設定の範囲からランダムに、互いに離して選ぶ。

    起動のたびに配置を変えて、決まった位置でしか動かない仕組みにならないようにするために使う。
    物体の向きは、腕の根元の方を向くようにそろえる。CRANE+ V2 は手首をひねれないので、
    真上から掴むときのグリッパの向きが、根元から物体への方向で決まるためである。

    Parameters
    ----------
    layout : dict
        load_layout で読んだ設定。
    rng : random.Random
        乱数の生成器。同じ種を渡せば同じ配置になる。
    max_attempts : int
        全部の物体を置けなかったときに、最初から選び直す回数の上限。
        物体 4 個では、100 回だと 200 通りの種のうち 23 通りで置けず、1000 回なら全部置けた
        （2026-10-27）。

    Returns
    -------
    list[ObjectPlacement]
        設定の objects と同じ順の、各物体の位置と向き。

    Raises
    ------
    RuntimeError
        上限の回数まで選び直しても、条件を満たす配置が見つからなかったとき。
    """
    labels: list[str] = [str(spec['label']) for spec in layout['objects']]
    for _ in range(max_attempts):
        placements = _try_place_all(labels, layout['spawn_area'], box_placements(layout), rng)
        if placements is not None:
            return placements
    raise RuntimeError('物体を置く位置が見つかりませんでした。spawn_area の範囲を広げてください')


def sample_distractor_placements(
    layout: dict, objects: list[ObjectPlacement], rng: random.Random, max_attempts: int = 1000
) -> list[ObjectPlacement]:
    """掴む対象ではない物体（distractors）を置く位置を、distractor_area からランダムに選ぶ。

    掴む物体（sample_object_placements で置いたもの）を置いたあとに、同じ乱数の生成器で選ぶ。
    設定に distractors がなければ、空のリストを返す。

    Parameters
    ----------
    layout : dict
        load_layout で読んだ設定。
    objects : list[ObjectPlacement]
        置き済みの掴む物体。これらからも distractor_area の min_separation だけ離す。
    rng : random.Random
        乱数の生成器。
    max_attempts : int
        全部を置けなかったときに、最初から選び直す回数の上限。

    Returns
    -------
    list[ObjectPlacement]
        設定の distractors と同じ順の、各物体の位置と向き。

    Raises
    ------
    RuntimeError
        上限の回数まで選び直しても、条件を満たす配置が見つからなかったとき。
    """
    labels: list[str] = [str(spec['label']) for spec in layout.get('distractors', [])]
    if not labels:
        return []
    for _ in range(max_attempts):
        placements = _try_place_all(
            labels, layout['distractor_area'], box_placements(layout), rng, objects
        )
        if placements is not None:
            return placements[len(objects):]
    raise RuntimeError(
        '掴む対象ではない物体を置く位置が見つかりませんでした。distractor_area の範囲を広げてください'
    )


def _try_place_all(
    labels: list[str],
    area: dict,
    boxes: list[BoxPlacement],
    rng: random.Random,
    placed: list[ObjectPlacement] | None = None,
) -> list[ObjectPlacement] | None:
    """物体を 1 個ずつ、置き済みの物体（placed）に足していく。置けなくなったら None を返す。"""
    placements: list[ObjectPlacement] = list(placed or [])
    for label in labels:
        placement = _try_place_one(label, area, boxes, placements, rng)
        if placement is None:
            return None
        placements.append(placement)
    return placements


def _try_place_one(
    label: str,
    area: dict,
    boxes: list[BoxPlacement],
    placed: list[ObjectPlacement],
    rng: random.Random,
    max_tries: int = 200,
) -> ObjectPlacement | None:
    """1 個の物体を、置き済みの物体と箱から離れた位置に置いてみる。"""
    for _ in range(max_tries):
        radius = rng.uniform(area['min_radius'], area['max_radius'])
        angle = math.radians(rng.uniform(area['min_angle_deg'], area['max_angle_deg']))
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        far_from_objects = all(
            math.hypot(x - other.x, y - other.y) >= area['min_separation'] for other in placed
        )
        far_from_boxes = all(
            math.hypot(x - box.x, y - box.y) >= area['min_box_distance'] for box in boxes
        )
        if far_from_objects and far_from_boxes:
            return ObjectPlacement(label, x, y, angle)
    return None
