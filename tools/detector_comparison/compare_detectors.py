"""capture_dataset.py で集めた画像で、検出モデルの見分けの正しさと速さを比べる。

比べられるのは OWLv2 と CLIP。3 つめに比べた YOLO-World は使わないことにして、ライブラリを
Docker イメージから外した（2026-10-27）。YOLO-World も含めて比べたときのコードは、
コミット b571f66 にある。

コンテナの中で、仮想環境の Python で実行する。

    $AI_VENV/bin/python tools/detector_comparison/compare_detectors.py \\
        /ws/tmp/dataset_shapes /ws/tmp/comparison

物体ごとに、モデルが見つけた枠の中心から位置を求め（物体を探すノードと同じ計算）、
Gazebo 上の正しい位置との水平の距離が tolerance 以内なら「正しく見つけた」とする。

Reads:
  - <データの場所>/*.png、*.json（capture_dataset.py の出力）
Writes:
  - <出力先>/results.json: 画像と物体ごとの結果
  - <出力先>/<モデル>_<文の組>_<画像>.png: 見つけた枠を描いた画像（最初の数枚）
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import time

from crane_sorter.camera_geometry import locate_on_plane, pixel_to_ray, rotate
from crane_sorter.detectors import create_detector, Detection
import cv2
import numpy as np

# 物体の名前の形の部分を、モデルに渡す英語にする言い方の組
_SHAPE_PHRASINGS: dict[str, dict[str, str]] = {
    'cube_cuboid': {'cube': 'cube', 'cuboid': 'cuboid'},
    'cube_rectangular': {'cube': 'cube', 'cuboid': 'rectangular block'},
    'square_long': {'cube': 'square block', 'cuboid': 'long block'},
}
# モデルごとの、枠を捨てる点数の下限。点数の意味はモデルごとに違う
_SCORE_THRESHOLDS = {'owlv2': 0.1, 'clip': 0.3}
_ANNOTATED_IMAGES = 3


@dataclass(frozen=True)
class _Sample:
    name: str
    rgb_image: np.ndarray
    metadata: dict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('dataset_dir', type=Path)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--models', nargs='+', default=['owlv2', 'clip'])
    parser.add_argument('--phrasings', nargs='+', default=list(_SHAPE_PHRASINGS))
    parser.add_argument('--plane-height', type=float, default=0.035,
                        help='枠の中心を通る視線と交わらせる平面の高さ [m]')
    parser.add_argument('--half-precision', action='store_true',
                        help='モデルの重みと計算を半精度（float16）にする')
    parser.add_argument('--tolerance', type=float, default=0.02,
                        help='正しく見つけたとみなす、正しい位置との水平の距離の上限 [m]')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    samples = _load_samples(args.dataset_dir)
    labels = sorted({item['label'] for sample in samples for item in sample.metadata['objects']})
    print(f'画像 {len(samples)} 枚、物体の名前 {labels}')

    results = []
    for model in args.models:
        for phrasing in args.phrasings:
            prompts = {label: _to_prompt(label, _SHAPE_PHRASINGS[phrasing]) for label in labels}
            result = _evaluate(model, phrasing, prompts, samples, args)
            results.append(result)
            print(_summary_line(result), flush=True)
            _release_gpu_memory()

    (args.output_dir / 'results.json').write_text(json.dumps(results, indent=2))
    print()
    print(_markdown_table(results, len(samples) * len(labels)))


def _load_samples(dataset_dir: Path) -> list[_Sample]:
    samples = []
    for metadata_path in sorted(dataset_dir.glob('*.json')):
        bgr_image = cv2.imread(str(metadata_path.with_suffix('.png')))
        samples.append(_Sample(
            metadata_path.stem,
            cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB),
            json.loads(metadata_path.read_text()),
        ))
    return samples


def _to_prompt(label: str, shape_words: dict[str, str]) -> str:
    """物体の名前（例: red_cuboid）を、モデルに渡す英語（例: red rectangular block）にする。"""
    color, shape = label.split('_', 1)
    return f'{color} {shape_words[shape]}'


def _evaluate(
    model: str, phrasing: str, prompts: dict[str, str], samples: list[_Sample], args
) -> dict:
    """1 つのモデルと言い方の組で、全部の画像を処理して結果をまとめる。"""
    load_start = time.perf_counter()
    detector = create_detector(
        model, prompts, _SCORE_THRESHOLDS[model], half_precision=args.half_precision
    )
    load_seconds = time.perf_counter() - load_start
    # 最初の 1 回は GPU の準備に時間がかかるので、速さの計測から外す
    detector.detect(samples[0].rgb_image)

    per_object = []
    latencies = []
    for index, sample in enumerate(samples):
        start = time.perf_counter()
        detections = detector.detect(sample.rgb_image)
        latencies.append(time.perf_counter() - start)
        by_label = {detection.label: detection for detection in detections}
        for truth in sample.metadata['objects']:
            per_object.append(_judge(sample, truth, by_label.get(truth['label']), args))
        if index < _ANNOTATED_IMAGES:
            annotated_path = args.output_dir / f'{model}_{phrasing}_{sample.name}.png'
            _save_annotated(sample, detections, annotated_path)
    del detector
    return {
        'model': model,
        'phrasing': phrasing,
        'prompts': prompts,
        'load_seconds': load_seconds,
        'median_latency_ms': 1000 * statistics.median(latencies),
        'objects': per_object,
    }


def _judge(sample: _Sample, truth: dict, detection: Detection | None, args) -> dict:
    """物体 1 個について、見つけた位置と正しい位置を比べる。"""
    result = {'image': sample.name, 'label': truth['label'], 'detected': detection is not None}
    if detection is None:
        result['outcome'] = 'missed'
        return result
    metadata = sample.metadata
    ray = pixel_to_ray(np.array(metadata['camera_matrix']), detection.center)
    rotation = np.array(metadata['camera_rotation_xyzw'])
    translation = np.array(metadata['camera_translation'])
    point_in_camera = locate_on_plane(ray, rotation, translation, args.plane_height)
    if point_in_camera is None:
        result['outcome'] = 'missed'
        return result
    point = rotate(rotation, point_in_camera) + translation
    error = float(np.hypot(point[0] - truth['x'], point[1] - truth['y']))
    result.update({
        'score': detection.score,
        'error_m': error,
        'outcome': 'correct' if error <= args.tolerance else 'wrong_place',
    })
    return result


def _save_annotated(sample: _Sample, detections: list[Detection], path: Path) -> None:
    image = cv2.cvtColor(sample.rgb_image, cv2.COLOR_RGB2BGR)
    for detection in detections:
        left, top, right, bottom = (int(value) for value in detection.box)
        cv2.rectangle(image, (left, top), (right, bottom), (0, 255, 255), 2)
        cv2.putText(image, f'{detection.label} {detection.score:.2f}', (left, max(top - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.imwrite(str(path), image)


def _release_gpu_memory() -> None:
    import gc

    import torch

    gc.collect()
    torch.cuda.empty_cache()


def _counts(result: dict) -> dict[str, int]:
    counts = {'correct': 0, 'wrong_place': 0, 'missed': 0}
    for item in result['objects']:
        counts[item['outcome']] += 1
    return counts


def _summary_line(result: dict) -> str:
    counts = _counts(result)
    return (f"{result['model']:10s} {result['phrasing']:16s} 正しい {counts['correct']:3d} "
            f"違う場所 {counts['wrong_place']:3d} 見つからない {counts['missed']:3d} "
            f"推論 {result['median_latency_ms']:.0f} ms")


def _markdown_table(results: list[dict], total: int) -> str:
    lines = [
        '| モデル | 形の言い方 | 正しく見つけた | 違う物体・場所 | 見つからない | '
        '正しいときの位置の誤差（中央値） | 1 枚の推論（中央値） |',
        '|---|---|---|---|---|---|---|',
    ]
    for result in results:
        counts = _counts(result)
        errors = [item['error_m'] for item in result['objects'] if item['outcome'] == 'correct']
        median_error = f'{1000 * statistics.median(errors):.0f} mm' if errors else '-'
        lines.append(
            f"| {result['model']} | {', '.join(result['prompts'].values())} | "
            f"{counts['correct']}/{total} | {counts['wrong_place']} | {counts['missed']} | "
            f"{median_error} | {result['median_latency_ms']:.0f} ms |"
        )
    return '\n'.join(lines)


if __name__ == '__main__':
    main()
