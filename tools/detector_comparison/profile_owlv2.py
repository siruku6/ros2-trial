"""OWLv2 の画像 1 枚の処理時間を、前処理・推論・後処理に分けて測る。

AI 版の物体を探すノードの処理を減らす方法を選ぶために使う（issue #6）。
コンテナの中で、仮想環境の Python で実行する。

    $AI_VENV/bin/python tools/detector_comparison/profile_owlv2.py /ws/tmp/dataset_shapes

精度（float32 と float16）ごとに、画像ごとの時間の中央値を表示する。
"""

import argparse
from pathlib import Path
import statistics
import time

import cv2
import numpy as np
import torch
from torchvision.ops import box_convert, nms
from transformers import Owlv2ForObjectDetection, Owlv2Processor

_MODEL_ID = 'google/owlv2-base-patch16-ensemble'
_PROMPTS = [['red square block', 'red long block', 'blue square block', 'green long block']]
_SCORE_THRESHOLD = 0.1
_NMS_IOU = 0.3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('dataset_dir', type=Path)
    args = parser.parse_args()
    images = [
        cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        for path in sorted(args.dataset_dir.glob('*.png'))
    ]
    processor = Owlv2Processor.from_pretrained(_MODEL_ID)
    print(f'画像 {len(images)} 枚、前処理の実装: {type(processor.image_processor).__name__}')
    for dtype in (torch.float32, torch.float16):
        model = Owlv2ForObjectDetection.from_pretrained(_MODEL_ID, dtype=dtype).to('cuda').eval()
        times = _profile(processor, model, dtype, images)
        print(f'{str(dtype):14s} ' + '  '.join(
            f'{name} {1000 * statistics.median(values):5.1f} ms' for name, values in times.items()
        ))
        del model
        torch.cuda.empty_cache()


def _profile(processor, model, dtype, images: list[np.ndarray]) -> dict[str, list[float]]:
    """画像ごとに、前処理・文の特徴量・推論全体・後処理の時間を測る。"""
    times: dict[str, list[float]] = {'前処理': [], '文の特徴量': [], '推論': [], '後処理': []}
    # 最初の 1 枚は GPU の準備に時間がかかるので、2 回目から測る
    for index, image in enumerate([images[0]] + images):
        start = time.perf_counter()
        inputs = processor(text=_PROMPTS, images=image, return_tensors='pt').to('cuda')
        inputs['pixel_values'] = inputs['pixel_values'].to(dtype)
        torch.cuda.synchronize()
        preprocessed = time.perf_counter()
        with torch.no_grad():
            model.owlv2.get_text_features(
                input_ids=inputs['input_ids'], attention_mask=inputs['attention_mask']
            )
            torch.cuda.synchronize()
            text_done = time.perf_counter()
            outputs = model(**inputs)
            torch.cuda.synchronize()
        inferred = time.perf_counter()
        scores = torch.sigmoid(outputs.logits[0].float())
        boxes = box_convert(outputs.pred_boxes[0].float(), 'cxcywh', 'xyxy') * max(image.shape[:2])
        best = scores.max(dim=1).values
        keep = best >= _SCORE_THRESHOLD
        kept = nms(boxes[keep], best[keep], _NMS_IOU)
        kept.cpu()
        finished = time.perf_counter()
        if index == 0:
            continue
        times['前処理'].append(preprocessed - start)
        times['文の特徴量'].append(text_done - preprocessed)
        times['推論'].append(inferred - text_done)
        times['後処理'].append(finished - inferred)
    return times


if __name__ == '__main__':
    main()
