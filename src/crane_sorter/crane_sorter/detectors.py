"""文で指定した物体を画像から探す検出モデル 2 つを、同じ形で呼べるようにまとめる。

どのモデルも、物体の名前（例: red_cube）と、それをモデルに渡す英語の文（例: "red cube"）の
組を受け取り、画像の中で見つけた物体の枠と名前を返す。新たな学習はせず、公開されている
学習済みの重みをそのまま使う。

- OWLv2: 文で指定した物体を枠で探す。Hugging Face Transformers の実装。AI 版はこれを使う
- CLIP: 画像と文の近さを点数にする。物体を探す機能はないので、色の鮮やかな画素の
  かたまりを候補として切り出し、候補ごとに各文との近さを点数にする

比べた 3 つめの YOLO-World は、色が同じで形の違う物体を見分けられず、使わないことにしたので
外した（2026-10-27。比べたときのコードはコミット b571f66 にある）。

どのモデルでも、まず物体の候補の枠ごとに、名前ごとの点数を求める。そのあと、同じ名前の物体は
机の上に 1 個しかないものとして、名前と枠を 1 対 1 に、点数の合計が最も大きくなるように
組み合わせる（assign_labels）。枠ごとに最も点数の高い名前を選ぶと、赤いキューブと赤い直方体の
ように色が同じで形の違う物体が、両方とも同じ名前になることが多かったためである（2026-10-27）。

torch などは NumPy 2 を要するので、このモジュールは仮想環境の Python から使う
（README の 3.3）。システムの Python からも読み込めるよう、torch などは使う関数の中で読む。
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

# 名前と組み合わせる候補の数の上限。組み合わせを総当たりで調べるので、多すぎると遅くなる
_MAX_CANDIDATES = 8


@dataclass(frozen=True)
class Detection:
    """見つけた物体 1 個。"""

    label: str
    score: float
    # 枠の左・上・右・下 [画素]
    box: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        """枠の中心 (u, v) [画素]。"""
        left, top, right, bottom = self.box
        return (left + right) / 2, (top + bottom) / 2


@dataclass(frozen=True)
class Candidate:
    """物体の候補の枠 1 つと、名前ごとの点数。"""

    # 枠の左・上・右・下 [画素]
    box: tuple[float, float, float, float]
    # 物体の名前と同じ順の点数
    scores: tuple[float, ...]


class Detector(Protocol):
    """検出モデルの共通の呼び方。"""

    labels: list[str]
    score_threshold: float

    def candidates(self, rgb_image: np.ndarray) -> list[Candidate]:
        """RGB の画像から物体の候補を探し、枠と名前ごとの点数を返す。"""
        ...

    def detect(self, rgb_image: np.ndarray) -> list[Detection]:
        """RGB の画像から物体を探し、名前ごとに多くとも 1 個の枠を返す。"""
        ...


class _DetectorBase:
    """候補を探す処理（candidates）から、名前を付けた枠を返す処理（detect）を作る。"""

    labels: list[str]
    score_threshold: float

    def candidates(self, rgb_image: np.ndarray) -> list[Candidate]:
        raise NotImplementedError

    def detect(self, rgb_image: np.ndarray) -> list[Detection]:
        candidates = self.candidates(rgb_image)
        assignment = assign_labels(candidates, self.labels, self.score_threshold)
        return to_detections(candidates, self.labels, assignment)


def create_detector(
    model: str,
    prompts: dict[str, str],
    score_threshold: float,
    device: str = 'cuda',
    half_precision: bool = False,
) -> Detector:
    """名前で選んだ検出モデルを用意する。初めて使うときは、重みを HF_HOME の下に取得する。

    Parameters
    ----------
    model : str
        "owlv2" か "clip"。
    prompts : dict[str, str]
        物体の名前から、モデルに渡す英語の文への対応。例: {"red_cube": "red cube"}
    score_threshold : float
        これより点数の低い組み合わせは使わない。点数の意味はモデルごとに違う。
    device : str
        推論に使う装置。"cuda" か "cpu"。
    half_precision : bool
        True なら、モデルの重みと計算を半精度（float16）にする。GPU で速くなる。

    Returns
    -------
    Detector
        detect を呼べるモデル。

    Raises
    ------
    ValueError
        知らないモデルの名前を渡したとき。
    """
    if model == 'owlv2':
        return Owlv2Detector(prompts, score_threshold, device, half_precision)
    if model == 'clip':
        return ClipDetector(prompts, score_threshold, device, half_precision)
    raise ValueError(f'検出モデル {model} は使えません。owlv2 か clip を選んでください')


def assign_labels(
    candidates: list[Candidate], labels: list[str], score_threshold: float
) -> dict[str, int]:
    """名前と候補の枠を 1 対 1 に、点数の合計が最も大きくなるように組み合わせる。

    同じ名前の物体は 1 個しかないものとする。点数が score_threshold より低い組み合わせは
    使わず、その名前は見つからなかったことにする。

    Parameters
    ----------
    candidates : list[Candidate]
        候補の枠と、labels と同じ順の点数。
    labels : list[str]
        物体の名前。
    score_threshold : float
        組み合わせに使う点数の下限。

    Returns
    -------
    dict[str, int]
        名前から、組み合わせた候補の番号（candidates の添字）への対応。
    """
    if not labels or not candidates:
        return {}
    # 最も点数の高い名前の点数で候補を絞り、総当たりの数を抑える
    order = sorted(
        range(len(candidates)), key=lambda index: max(candidates[index].scores), reverse=True
    )[:_MAX_CANDIDATES]
    best_total = 0.0
    best_choice: list[int | None] = [None] * len(labels)

    def search(label_index: int, used: frozenset[int], total: float, choice: list) -> None:
        nonlocal best_total, best_choice
        if label_index == len(labels):
            if total > best_total:
                best_total, best_choice = total, list(choice)
            return
        # この名前には枠を割り当てない場合
        search(label_index + 1, used, total, choice + [None])
        for candidate_index in order:
            score = candidates[candidate_index].scores[label_index]
            if candidate_index in used or score < score_threshold:
                continue
            search(label_index + 1, used | {candidate_index}, total + score,
                   choice + [candidate_index])

    search(0, frozenset(), 0.0, [])
    return {label: index for label, index in zip(labels, best_choice) if index is not None}


def to_detections(
    candidates: list[Candidate], labels: list[str], assignment: dict[str, int]
) -> list[Detection]:
    """名前と候補の対応を、名前・点数・枠の組にする。"""
    return [
        Detection(label, candidates[index].scores[labels.index(label)], candidates[index].box)
        for label, index in assignment.items()
    ]


class Owlv2Detector(_DetectorBase):
    """OWLv2（Hugging Face Transformers）で物体を探す。"""

    _MODEL_ID = 'google/owlv2-base-patch16-ensemble'
    # 重なった枠を 1 つにまとめるときの、重なりの割合（IoU）の上限
    _NMS_IOU = 0.3

    def __init__(
        self, prompts: dict[str, str], score_threshold: float, device: str, half_precision: bool
    ) -> None:
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.labels = list(prompts)
        self._texts = [list(prompts.values())]
        self._dtype = torch.float16 if half_precision else torch.float32
        self._processor = Owlv2Processor.from_pretrained(self._MODEL_ID)
        self._model = Owlv2ForObjectDetection.from_pretrained(
            self._MODEL_ID, dtype=self._dtype
        ).to(device).eval()
        self.score_threshold = score_threshold
        self._device = device

    def candidates(self, rgb_image: np.ndarray) -> list[Candidate]:
        import torch
        from torchvision.ops import box_convert, nms

        inputs = self._processor(text=self._texts, images=rgb_image, return_tensors='pt')
        inputs = inputs.to(self._device)
        inputs['pixel_values'] = inputs['pixel_values'].to(self._dtype)
        with torch.no_grad():
            outputs = self._model(**inputs)
        # 画像を細かく区切った区画ごとに、枠 1 つと、文ごとの点数が出る。後処理は単精度で行う
        scores = torch.sigmoid(outputs.logits[0].float())
        # OWLv2 は画像を正方形に埋めてから処理するので、枠は正方形の大きさで画素に戻す
        side = max(rgb_image.shape[:2])
        boxes = box_convert(outputs.pred_boxes[0].float(), 'cxcywh', 'xyxy') * side
        best_scores = scores.max(dim=1).values
        keep = best_scores >= self.score_threshold
        boxes, scores, best_scores = boxes[keep], scores[keep], best_scores[keep]
        keep = nms(boxes, best_scores, self._NMS_IOU)
        return [
            Candidate(tuple(map(float, box)), tuple(map(float, row)))
            for box, row in zip(boxes[keep].cpu().numpy(), scores[keep].cpu().numpy())
        ]


class ClipDetector(_DetectorBase):
    """色の鮮やかな画素のかたまりを候補にし、CLIP で候補ごとに各文との近さを点数にする。"""

    _MODEL_ID = 'openai/clip-vit-large-patch14'
    # 候補にする画素の、彩度と明度の下限（OpenCV の HSV、0〜255）。Gazebo の画像では、
    # 物体は彩度 176 以上・明度 172 以上、机の木目は彩度 155 以下・明度 94 前後だった
    _MIN_SATURATION = 165
    _MIN_VALUE = 120
    # 候補にするかたまりの最小の画素数
    _MIN_AREA = 300
    # 切り出すときに、かたまりの外接矩形の周りに足す余白 [画素]
    _CROP_MARGIN = 12
    # どの物体でもない候補を捨てるために、一緒に比べる文
    _NEGATIVE_PROMPTS = ('wooden table', 'gray tray', 'robot arm')

    def __init__(
        self, prompts: dict[str, str], score_threshold: float, device: str, half_precision: bool
    ) -> None:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.labels = list(prompts)
        texts = [f'a photo of a {text}' for text in prompts.values()]
        texts += [f'a photo of a {text}' for text in self._NEGATIVE_PROMPTS]
        self._dtype = torch.float16 if half_precision else torch.float32
        self._processor = CLIPProcessor.from_pretrained(self._MODEL_ID)
        self._model = CLIPModel.from_pretrained(
            self._MODEL_ID, dtype=self._dtype
        ).to(device).eval()
        with torch.no_grad():
            text_inputs = self._processor(text=texts, return_tensors='pt', padding=True)
            # Transformers 5 では、射影したあとの特徴量は pooler_output に入る
            text_features = self._model.get_text_features(**text_inputs.to(device)).pooler_output
        self._text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        self.score_threshold = score_threshold
        self._device = device

    def candidates(self, rgb_image: np.ndarray) -> list[Candidate]:
        import torch

        boxes = self._propose(rgb_image)
        if not boxes:
            return []
        crops = [rgb_image[top:bottom, left:right] for left, top, right, bottom in boxes]
        with torch.no_grad():
            image_inputs = self._processor(images=crops, return_tensors='pt').to(self._device)
            image_inputs['pixel_values'] = image_inputs['pixel_values'].to(self._dtype)
            image_features = self._model.get_image_features(**image_inputs).pooler_output
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            scale = self._model.logit_scale.exp()
            logits = scale * image_features @ self._text_features.T
            probabilities = logits.float().softmax(dim=-1)
        return [
            Candidate(tuple(map(float, box)), tuple(map(float, row[:len(self.labels)])))
            for box, row in zip(boxes, probabilities.cpu().numpy())
            # 最も近い文が、物体ではない文（机など）なら候補から外す
            if int(row.argmax()) < len(self.labels)
        ]

    def _propose(self, rgb_image: np.ndarray) -> list[tuple[int, int, int, int]]:
        """色の鮮やかな画素のかたまりを、余白を足した切り出しの範囲（左・上・右・下）で返す。"""
        import cv2

        hsv_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2HSV)
        mask = (
            (hsv_image[:, :, 1] >= self._MIN_SATURATION) & (hsv_image[:, :, 2] >= self._MIN_VALUE)
        ).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        height, width = mask.shape
        boxes = []
        for index in range(1, count):
            left, top, box_width, box_height, area = (int(value) for value in stats[index])
            if area < self._MIN_AREA:
                continue
            boxes.append((
                max(left - self._CROP_MARGIN, 0),
                max(top - self._CROP_MARGIN, 0),
                min(left + box_width + self._CROP_MARGIN, width),
                min(top + box_height + self._CROP_MARGIN, height),
            ))
        return boxes
