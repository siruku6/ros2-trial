"""検出モデルの見つけた候補に、前の画像までと矛盾しない物体の名前を付ける。

机の上の物体は、腕が動かさない限り動かない。そこで、物体の名前を、その物体を最後に見た
位置と結び付けて覚えておく。新しい画像で、覚えている位置の近くに候補があれば、同じ名前を
付ける。どの名前の位置にも近くない候補には、空いている名前から、名前と候補を 1 対 1 に
組み合わせて付ける（detectors.assign_labels）。

検出モデルの点数だけで毎回名前を付け直すと、赤い直方体を箱に入れたとたん、箱の中の直方体と
机の上の赤いキューブとで名前が入れ替わることがあった（2026-10-27）。箱の中では物体の見え方が
変わり、「キューブらしさ」の相対的な高さが逆転したためである。
"""

from dataclasses import dataclass
import math

from crane_sorter.detectors import assign_labels, Candidate


@dataclass(frozen=True)
class Observation:
    """候補 1 つと、その位置（crane_plus_base から見た x, y [m]）。"""

    candidate: Candidate
    x: float
    y: float


@dataclass
class _Memory:
    """名前を付けた物体を最後に見た位置 [m] と時刻 [s]。"""

    x: float
    y: float
    seen_at: float


class LabelTracker:
    """物体の名前と、その物体を最後に見た位置を覚え、候補に名前を付ける。

    Parameters
    ----------
    labels : list[str]
        物体の名前。候補の点数と同じ順にする。
    score_threshold : float
        名前を付けるのに要る点数の下限。覚えている位置の近くの候補でも、その名前の点数が
        これより低ければ、名前を付けない（別の物体が同じ場所に置かれたときのため）。
    match_distance : float
        覚えている位置と候補の位置が、この距離 [m] 以内なら同じ物体とみなす。
    release_seconds : float
        この時間 [s] より長く見えていない名前は、空いている名前として、ほかの候補に
        付けられるようにする。腕に一瞬隠れただけの物体の名前を、運ばれている物体に
        取られないよう、すぐには空けない。
    """

    def __init__(
        self,
        labels: list[str],
        score_threshold: float,
        match_distance: float = 0.025,
        release_seconds: float = 2.0,
    ) -> None:
        self._labels = labels
        self._score_threshold = score_threshold
        self._match_distance = match_distance
        self._release_seconds = release_seconds
        self._memories: dict[str, _Memory] = {}

    def update(self, observations: list[Observation], now: float) -> dict[str, int]:
        """候補に名前を付け、名前から候補の番号（observations の添字）への対応を返す。

        Parameters
        ----------
        observations : list[Observation]
            1 枚の画像で見つけた候補と位置。
        now : float
            画像を撮った時刻 [s]。

        Returns
        -------
        dict[str, int]
            名前を付けた候補。名前ごとに多くとも 1 つ。
        """
        assigned = self._match_remembered(observations)

        # 覚えている位置の近くになかった候補に、空いている名前を組み合わせる
        free_labels = [
            label for label in self._labels
            if label not in assigned and self._is_free(label, now)
        ]
        free_indices = [
            index for index in range(len(observations)) if index not in assigned.values()
        ]
        label_indices = [self._labels.index(label) for label in free_labels]
        free_candidates = [
            Candidate(
                observations[index].candidate.box,
                tuple(observations[index].candidate.scores[i] for i in label_indices),
            )
            for index in free_indices
        ]
        for label, index in assign_labels(
            free_candidates, free_labels, self._score_threshold
        ).items():
            assigned[label] = free_indices[index]

        for label, index in assigned.items():
            self._memories[label] = _Memory(observations[index].x, observations[index].y, now)
        return assigned

    def _is_free(self, label: str, now: float) -> bool:
        """まだ見たことがないか、しばらく見えていない名前なら True を返す。"""
        memory = self._memories.get(label)
        return memory is None or now - memory.seen_at > self._release_seconds

    def _match_remembered(self, observations: list[Observation]) -> dict[str, int]:
        """覚えている位置の近くにある候補に、その名前を付ける。近いものから順に決める。"""
        pairs = []
        for label, memory in self._memories.items():
            label_index = self._labels.index(label)
            for index, observation in enumerate(observations):
                distance = math.hypot(observation.x - memory.x, observation.y - memory.y)
                score = observation.candidate.scores[label_index]
                if distance <= self._match_distance and score >= self._score_threshold:
                    pairs.append((distance, label, index))
        assigned: dict[str, int] = {}
        for _, label, index in sorted(pairs):
            if label not in assigned and index not in assigned.values():
                assigned[label] = index
        return assigned
