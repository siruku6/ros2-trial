"""録画（record_demo.sh の出力）から、提出用の動画を作る。

コンテナの中で、仮想環境の Python で実行する（Pillow と PyYAML を使う）。

    $AI_VENV/bin/python tools/video/edit_video.py /ws/tmp/video/take4 /ws/tmp/video/draft.mp4

画面の録画から Gazebo・検出結果・端末を切り出して 1 枚に並べ、いま実行している指示の文を
字幕にする。動画の全体を同じ速さで再生する。画面に出す文字と速さは video_text.yaml にある。
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont
import yaml

_TEXT_FILE = Path(__file__).with_name('video_text.yaml')
_FONT_REGULAR = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
_FONT_BOLD = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
_SIZE = (1920, 1080)
_FPS = 30

_BACKGROUND = (21, 24, 29)
_PANEL = (32, 36, 43)
_TEXT = (240, 242, 245)
_ACCENT = (255, 204, 51)

# 録画の画面から切り出す範囲（x, y, 幅, 高さ）。record_demo.sh の窓の配置に合わせてある。
# 検出結果の画像は、下の 24 ピクセル（腕の根元が写る所）が端末の窓の枠に隠れるので、その上まで
_GAZEBO_CROP = (0, 110, 1280, 880)
_DETECTION_CROP = (1280, 160, 640, 456)
_TERMINAL_CROP = (1281, 679, 634, 372)
# 切り出しを並べる位置（x, y）。検出結果の位置は、端末を出すかで変わる
_GAZEBO_AT = (0, 0)
_DETECTION_AT = (1280, 46)
_DETECTION_ALONE_AT = (1280, 260)
_TERMINAL_AT = (1283, 578)
# 下の字幕の帯
_CAPTION_TOP = 880

# Gazebo の切り出しの中で、箱の札を置く位置（乱数の種 24 の配置、視点は record_demo.sh のもの）。
# 腕が箱の上に来ても隠れないよう、箱の外側に置く
_RIGHT_BOX_AT = (110, 600)
_LEFT_BOX_AT = (1172, 592)


@dataclass
class Segment:
    """動画の 1 区切り。録画の一部（start から duration 秒）か、最後の画面（card）。"""

    duration: float
    overlay: Path
    start: float = 0.0
    card: bool = False
    fade_in: bool = False
    fade_out: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('take_dir', type=Path, help='record_demo.sh の出力先')
    parser.add_argument('output', type=Path, help='作る MP4 のファイル')
    parser.add_argument('--text', type=Path, default=_TEXT_FILE, help='画面に出す文字と速さ')
    args = parser.parse_args()
    texts = yaml.safe_load(args.text.read_text())
    speed = float(texts['speed'])
    instructions = read_instructions(args.take_dir)
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        save(screen_base(texts), work_dir / 'base.png')
        segments = plan_segments(instructions, texts, work_dir)
        parts = [
            encode_segment(
                args.take_dir / 'screen.mkv', segment, speed, bool(texts['show_terminal']), work_dir, k
            )
            for k, segment in enumerate(segments)
        ]
        concatenate(parts, args.output)
    total = sum(s.duration if s.card else s.duration / speed for s in segments)
    print(f'{args.output} を作りました（{total:.1f} 秒、{speed:g} 倍速）')


def read_instructions(take_dir: Path) -> list[tuple[str, float, float]]:
    """指示の記録を読み、指示ごとに（指示の文、始まり、終わり）を、録画の始まりからの秒数で返す。"""
    record_start = float((take_dir / 'record_start.txt').read_text())
    instructions: list[tuple[str, float, float]] = []
    for line in (take_dir / 'instructions.log').read_text().splitlines():
        stamp, _, message = line.partition(' ')
        time = float(stamp) - record_start
        body = message[message.find(']') + 1:].strip()
        if body.startswith('「'):
            instructions.append((body[1:body.index('」')], time, time))
        elif body.startswith(('成功', '失敗', '取り消し')) and instructions:
            text, start, _ = instructions[-1]
            instructions[-1] = (text, start, time)
    return instructions


def plan_segments(
    instructions: list[tuple[str, float, float]], texts: dict, work_dir: Path
) -> list[Segment]:
    """動画の区切りを順に並べる。区切りごとに、録画に重ねる字幕の画像も作る。"""
    speed = float(texts['speed'])
    first_start = instructions[0][1]
    intro_start = max(0.5, first_start - texts['intro_seconds'] * speed)
    segments = [Segment(
        first_start - intro_start, save(screen_overlay(texts['title'], texts), work_dir / 'intro.png'),
        start=intro_start, fade_in=True,
    )]
    for index, (text, start, end) in enumerate(instructions, start=1):
        # 次の指示の始まりまでを 1 つの区切りにし、録画を切れ目なく使う
        next_start = instructions[index][1] if index < len(instructions) else end
        caption = texts['instruction_caption'].format(index=index, total=len(instructions), text=text)
        overlay = save(screen_overlay(caption, texts), work_dir / f'instruction{index}.png')
        segments.append(Segment(next_start - start, overlay, start=start))
    last_end = instructions[-1][2]
    has_end_card = bool(texts['end_card'])
    segments.append(Segment(
        texts['closing_seconds'] * speed,
        save(screen_overlay(texts['closing_caption'], texts), work_dir / 'closing.png'),
        start=last_end, fade_out=has_end_card,
    ))
    if has_end_card:
        segments.append(Segment(
            float(texts['end_card_seconds']), save(end_card(texts['end_card']), work_dir / 'end.png'),
            card=True, fade_in=True,
        ))
    return segments


# ---- 画面の部品 ----

def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REGULAR, size, index=0)


def save(image: Image.Image, path: Path) -> Path:
    image.save(path)
    return path


def pill(
    draw: ImageDraw.ImageDraw, center: tuple[int, int], text: str, size: int,
    fill: tuple, text_fill: tuple,
) -> None:
    """角の丸い札を、center を中心に描く。"""
    text_font = font(size, bold=True)
    left, top, right, bottom = draw.textbbox(center, text, font=text_font, anchor='mm')
    pad_x, pad_y = size // 2, size // 4
    draw.rounded_rectangle(
        (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y), radius=size // 3, fill=fill
    )
    draw.text(center, text, font=text_font, fill=text_fill, anchor='mm')


def screen_base(texts: dict) -> Image.Image:
    """録画の切り出しを並べる下地。右側の見出しを描く。"""
    image = Image.new('RGB', _SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(image)
    detection_y = _DETECTION_AT[1] if texts['show_terminal'] else _DETECTION_ALONE_AT[1]
    heading(draw, (1280, detection_y - 46, 1920, detection_y), texts['detection_heading'])
    if texts['show_terminal']:
        heading(draw, (1280, _TERMINAL_AT[1] - 48, 1920, _TERMINAL_AT[1] - 2), texts['terminal_heading'])
    return image


def heading(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str) -> None:
    if text:
        draw.rectangle(box, fill=_PANEL)
        draw.text((box[0] + 16, (box[1] + box[3]) // 2), text, font=font(24, True), fill=_TEXT, anchor='lm')


def screen_overlay(caption: str, texts: dict) -> Image.Image:
    """録画の上に重ねる画像（透明の部分は録画が見える）。見出し、箱の札、速さ、字幕を描く。"""
    image = Image.new('RGBA', _SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Gazebo の画面の左上にある道具の帯を隠し、見出しを書く
    draw.rectangle((0, 0, 420, 104), fill=(*_BACKGROUND, 255))
    if texts['gazebo_heading']:
        draw.text((20, 52), texts['gazebo_heading'], font=font(26, True), fill=_TEXT, anchor='lm')
    for label, center in ((texts['right_box_label'], _RIGHT_BOX_AT), (texts['left_box_label'], _LEFT_BOX_AT)):
        if label:
            pill(draw, center, label, 28, (255, 255, 255, 230), (20, 20, 20))
    speed = float(texts['speed'])
    if speed != 1.0 and texts['speed_label']:
        label = texts['speed_label'].format(speed=f'{speed:g}')
        pill(draw, (1180, 48), label, 30, (*_ACCENT, 245), (20, 20, 20))
    draw.rectangle((0, _CAPTION_TOP, 1280, _SIZE[1]), fill=(*_BACKGROUND, 255))
    if caption:
        draw.text((40, (_CAPTION_TOP + _SIZE[1]) // 2), caption, font=font(46, True), fill=_TEXT, anchor='lm')
    return image


def end_card(lines: list[str]) -> Image.Image:
    image = Image.new('RGB', _SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(image)
    top = _SIZE[1] // 2 - 40 * (len(lines) - 1)
    for k, line in enumerate(lines):
        draw.text((_SIZE[0] // 2, top + 80 * k), line, font=font(48, True), fill=_TEXT, anchor='mm')
    return image


# ---- ffmpeg ----

def encode_segment(
    video: Path, segment: Segment, speed: float, show_terminal: bool, work_dir: Path, number: int
) -> Path:
    """1 つの区切りを、同じ形式（1920 × 1080、30 fps、H.264）の MP4 にする。"""
    output = work_dir / f'part{number:02d}.mp4'
    length = segment.duration if segment.card else segment.duration / speed
    fades = []
    if segment.fade_in:
        fades.append('fade=in:st=0:d=0.5')
    if segment.fade_out:
        fades.append(f'fade=out:st={length - 0.5:.3f}:d=0.5')
    fade = ''.join(f',{f}' for f in fades)
    if segment.card:
        inputs = ['-loop', '1', '-framerate', str(_FPS), '-t', f'{length:.3f}', '-i', str(segment.overlay)]
        graph = f'[0:v]format=yuv420p{fade}[v]'
    else:
        inputs = [
            '-ss', f'{segment.start:.3f}', '-t', f'{segment.duration:.3f}', '-i', str(video),
            '-loop', '1', '-i', str(work_dir / 'base.png'),
            '-loop', '1', '-i', str(segment.overlay),
        ]
        graph = screen_graph(speed, show_terminal) + fade + '[v]'
    _ffmpeg(inputs + [
        '-filter_complex', graph, '-map', '[v]', '-t', f'{length:.3f}',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', str(_FPS),
        '-video_track_timescale', '15360', str(output),
    ])
    return output


def screen_graph(speed: float, show_terminal: bool) -> str:
    """録画（入力 0）を切り出して下地（入力 1）に並べ、字幕（入力 2）を重ねて、速さを変える。"""
    detection_at = _DETECTION_AT if show_terminal else _DETECTION_ALONE_AT
    pieces = [
        '[0:v]split=2[s1][s2]',
        f'[s1]crop={_crop(_GAZEBO_CROP)}[gz]',
        f'[s2]crop={_crop(_DETECTION_CROP)}[det]',
        f'[1:v][gz]overlay={_GAZEBO_AT[0]}:{_GAZEBO_AT[1]}:shortest=1[a]',
        f'[a][det]overlay={detection_at[0]}:{detection_at[1]}[b]',
    ]
    if show_terminal:
        pieces[0] = '[0:v]split=3[s1][s2][s3]'
        pieces.append(f'[s3]crop={_crop(_TERMINAL_CROP)}[term]')
        pieces.append(f'[b][term]overlay={_TERMINAL_AT[0]}:{_TERMINAL_AT[1]}[c]')
    else:
        pieces.append('[b]null[c]')
    pieces.append(f'[c][2:v]overlay=0:0,setpts=(PTS-STARTPTS)/{speed},fps={_FPS},format=yuv420p')
    return ';'.join(pieces)


def concatenate(parts: list[Path], output: Path) -> None:
    listing = parts[0].parent / 'parts.txt'
    listing.write_text(''.join(f"file '{part}'\n" for part in parts))
    output.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy',
             '-movflags', '+faststart', str(output)])


def _crop(crop: tuple[int, int, int, int]) -> str:
    x, y, width, height = crop
    return f'{width}:{height}:{x}:{y}'


def _ffmpeg(arguments: list[str]) -> None:
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y'] + arguments, check=True)


if __name__ == '__main__':
    main()
