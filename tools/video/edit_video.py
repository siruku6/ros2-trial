"""録画（record_demo.sh の出力）から、字幕と説明の画面を付けた提出用の動画を作る。

コンテナの中で、仮想環境の Python で実行する（Pillow を使う）。

    $AI_VENV/bin/python tools/video/edit_video.py /ws/tmp/video/take2 /ws/tmp/video/draft.mp4

画面の録画から Gazebo・検出結果・端末の 3 か所を切り出して 1 枚に並べ、字幕を重ねる。
腕が動く間は 2 倍速にし、画面に「2倍速」と出す（課題 PDF は倍速を 2 倍まで認め、明記を求める）。
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONT_REGULAR = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
_FONT_BOLD = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
_SIZE = (1920, 1080)
_FPS = 30

_BACKGROUND = (21, 24, 29)
_PANEL = (32, 36, 43)
_TEXT = (240, 242, 245)
_SUBTEXT = (180, 188, 199)
_ACCENT = (255, 204, 51)

# 録画の画面から切り出す範囲（x, y, 幅, 高さ）と、並べる位置（x, y）。
# record_demo.sh の窓の配置に合わせてある
_GAZEBO_CROP = (0, 110, 1280, 880)
_GAZEBO_AT = (0, 0)
_DETECTION_CROP = (1280, 160, 640, 474)
_DETECTION_AT = (1280, 46)
_TERMINAL_CROP = (1281, 679, 634, 372)
_TERMINAL_AT = (1283, 578)

# Gazebo の切り出しの中で、箱の名前と赤い物体の名前を置く位置（乱数の種 24 の配置、視点は
# record_demo.sh のもの）。箱の名前は、腕が箱の上に来ても隠れないよう、箱の外側に置く
_BOX_LABELS = {'右の箱': (110, 600), '左の箱': (1172, 592)}
_RED_LABELS = {'赤い直方体': (445, 618), '赤いキューブ': (808, 655)}

_INSTRUCTION_NOTES = {
    1: '赤い 2 個のうち、長い方（直方体）を掴む',
    2: '「ブロック」はキューブのこと。箱の中の直方体と取り違えずに、赤いキューブを掴む',
    3: '青いキューブを左の箱へ',
    4: '緑の直方体を右の箱へ',
}
# 掴む瞬間と放す瞬間を等速で見せる指示。ほかの指示は全体を 2 倍速にする
_REAL_SPEED_INSTRUCTIONS = (1, 2)


@dataclass
class Instruction:
    """指示 1 件の、録画の始まりからの各段階の時刻 [s]。"""

    index: int
    text: str
    start: float
    stages: dict[str, float] = field(default_factory=dict)
    end: float = 0.0


@dataclass
class Segment:
    """動画の 1 区切り。説明の画面（card）か、録画の一部（screen）。"""

    duration: float
    card: Path | None = None
    start: float = 0.0
    speed: float = 1.0
    overlay: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('take_dir', type=Path, help='record_demo.sh の出力先')
    parser.add_argument('output', type=Path, help='作る MP4 のファイル')
    args = parser.parse_args()
    instructions = read_instructions(args.take_dir)
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        segments = plan_segments(args.take_dir, instructions, work_dir)
        parts = [
            encode_segment(args.take_dir / 'screen.mkv', segment, work_dir / f'part{k:02d}.mp4')
            for k, segment in enumerate(segments)
        ]
        concatenate(parts, args.output)
    total = sum(segment.duration / segment.speed for segment in segments)
    print(f'{args.output} を作りました（{total:.1f} 秒）')


def read_instructions(take_dir: Path) -> list[Instruction]:
    """指示の記録を読み、指示ごとの各段階の時刻を、録画の始まりからの秒数で返す。"""
    record_start = float((take_dir / 'record_start.txt').read_text())
    instructions: list[Instruction] = []
    for line in (take_dir / 'instructions.log').read_text().splitlines():
        stamp, _, message = line.partition(' ')
        time = float(stamp) - record_start
        if message == 'done' or not message.startswith('['):
            continue
        index = int(message[1:message.index('/')])
        body = message[message.index(']') + 1:].strip()
        if body.startswith('「'):
            instructions.append(Instruction(index, body[1:body.index('」')], time))
        elif body.startswith('段階:'):
            instructions[-1].stages[body.split(':')[1].strip()] = time
        else:
            instructions[-1].end = time
    return instructions


def plan_segments(
    take_dir: Path, instructions: list[Instruction], work_dir: Path
) -> list[Segment]:
    """動画の区切りを順に並べる。説明の画面と字幕の画像も、ここで作る。"""
    title_background = grab_frame(take_dir / 'screen.mkv', 3.0, work_dir / 'title_bg.png')
    first = instructions[0].start
    segments = [
        Segment(6.0, card=save(title_card(title_background), work_dir / 'title.png')),
        screen_segment(
            0.5, first * 0.45, 1.0, work_dir / 'intro1.png',
            '机の上に、物体 4 個と左右の箱 2 個', '物体の配置は、起動のたびに乱数で決まる',
        ),
        screen_segment(
            first * 0.45, first - 0.5, 1.0, work_dir / 'intro2.png',
            '赤いキューブと赤い直方体は、色が同じ',
            '色だけでは見分けられないので、文で物体を探す AI（OWLv2）で見分ける',
            red_labels=True,
        ),
        Segment(14.0, card=save(system_card(), work_dir / 'system.png')),
        Segment(14.0, card=save(ideas_card(), work_dir / 'ideas.png')),
    ]
    for instruction in instructions:
        segments += instruction_segments(instruction, len(instructions), work_dir)
    last_end = instructions[-1].end
    segments.append(screen_segment(
        last_end, last_end + 2.5, 1.0, work_dir / 'done.png',
        '4 個すべてを、指示どおりの箱に入れた', '',
    ))
    segments.append(Segment(12.0, card=save(results_card(), work_dir / 'results.png')))
    segments.append(Segment(7.0, card=save(credits_card(), work_dir / 'credits.png')))
    return segments


def instruction_segments(
    instruction: Instruction, total: int, work_dir: Path
) -> list[Segment]:
    """指示 1 件の区切り。腕が動く間は 2 倍速、掴む瞬間と放す瞬間は等速にする。"""
    heading = f'指示 {instruction.index}/{total}「{instruction.text}」'
    note = _INSTRUCTION_NOTES.get(instruction.index, '')
    stages = instruction.stages
    if instruction.index in _REAL_SPEED_INSTRUCTIONS:
        bounds = [
            (instruction.start, stages['grasping'], 2.0),
            (stages['grasping'], stages['carrying'], 1.0),
            (stages['carrying'], stages['releasing'], 2.0),
            (stages['releasing'], stages['returning'], 1.0),
            (stages['returning'], instruction.end, 2.0),
        ]
    else:
        bounds = [(instruction.start, instruction.end, 2.0)]
    return [
        screen_segment(
            start, end, speed, work_dir / f'inst{instruction.index}_{k}.png', heading, note
        )
        for k, (start, end, speed) in enumerate(bounds)
    ]


def screen_segment(
    start: float, end: float, speed: float, overlay_path: Path,
    heading: str, note: str, red_labels: bool = False,
) -> Segment:
    overlay = screen_overlay(heading, note, speed, red_labels)
    return Segment(end - start, start=start, speed=speed, overlay=save(overlay, overlay_path))


# ---- 画面の部品 ----

def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REGULAR, size, index=0)


def save(image: Image.Image, path: Path) -> Path:
    image.save(path)
    return path


def pill(
    draw: ImageDraw.ImageDraw, center: tuple[int, int], text: str, size: int,
    fill: tuple, text_fill: tuple, pointer: bool = False,
) -> None:
    """角の丸い札を、center を中心に描く。pointer なら下向きの三角を付ける。"""
    text_font = font(size, bold=True)
    left, top, right, bottom = draw.textbbox(center, text, font=text_font, anchor='mm')
    pad_x, pad_y = size // 2, size // 4
    box = (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)
    draw.rounded_rectangle(box, radius=size // 3, fill=fill)
    if pointer:
        x, y = center[0], box[3]
        draw.polygon([(x - 10, y), (x + 10, y), (x, y + 14)], fill=fill)
    draw.text(center, text, font=text_font, fill=text_fill, anchor='mm')


def screen_base() -> Image.Image:
    """録画の切り出しを並べる下地。右側の見出しと、右下の補足を描く。"""
    image = Image.new('RGB', _SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1280, 0, 1920, 45), fill=_PANEL)
    draw.text((1296, 22), 'カメラ画像と OWLv2 の検出結果', font=font(24, True), fill=_TEXT, anchor='lm')
    draw.text((1904, 22), '腕が止まっているときに更新', font=font(17), fill=_SUBTEXT, anchor='rm')
    draw.rectangle((1280, 530, 1920, 575), fill=_PANEL)
    draw.text((1296, 552), '指示を送る端末（action の途中経過）', font=font(24, True), fill=_TEXT, anchor='lm')
    draw.text((1600, 1000), 'ROS 2 Jazzy ・ Gazebo シミュレーション', font=font(20), fill=_SUBTEXT, anchor='mm')
    draw.text((1600, 1036), 'github.com/siruku6/ros2-trial', font=font(20), fill=_SUBTEXT, anchor='mm')
    return image


def screen_overlay(heading: str, note: str, speed: float, red_labels: bool) -> Image.Image:
    """録画の上に重ねる、字幕・速さ・箱の名前の画像（透明の部分は録画が見える）。"""
    image = Image.new('RGBA', _SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Gazebo の画面の左上の道具の帯を隠し、何の画面かを書く
    draw.rectangle((0, 0, 420, 104), fill=(*_BACKGROUND, 240))
    draw.text((20, 34), 'Gazebo（シミュレーション）', font=font(26, True), fill=_TEXT, anchor='lm')
    draw.text((20, 74), '左右は腕から見た向き', font=font(20), fill=_SUBTEXT, anchor='lm')
    for text, center in _BOX_LABELS.items():
        pill(draw, center, text, 28, (255, 255, 255, 230), (20, 20, 20))
    if red_labels:
        for text, center in _RED_LABELS.items():
            pill(draw, center, text, 28, (*_ACCENT, 245), (20, 20, 20), pointer=True)
    if speed != 1.0:
        pill(draw, (1180, 48), f'{speed:g}倍速', 30, (*_ACCENT, 245), (20, 20, 20))
    else:
        pill(draw, (1180, 48), '等速', 30, (255, 255, 255, 220), (20, 20, 20))
    # 下の字幕の帯
    draw.rectangle((0, 880, 1280, 1080), fill=(*_BACKGROUND, 255))
    draw.text((40, 935), heading, font=font(42, True), fill=_TEXT, anchor='lm')
    if note:
        draw.text((40, 1012), note, font=font(30), fill=_SUBTEXT, anchor='lm')
    return image


# ---- 説明の画面 ----

def card_canvas(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new('RGB', _SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((120, 110), title, font=font(52, True), fill=_TEXT, anchor='lm')
    draw.line((120, 160, 1800, 160), fill=_ACCENT, width=4)
    return image, draw


def title_card(background_path: Path) -> Image.Image:
    # 背景は、Gazebo の画面のうち、左上の道具の帯より下の 16:9 の範囲をぼかしたもの
    background = Image.open(background_path).convert('RGB')
    gazebo = background.crop((0, 230, 1280, 950)).resize(_SIZE).filter(ImageFilter.GaussianBlur(6))
    image = Image.blend(Image.new('RGB', _SIZE, _BACKGROUND), gazebo, 0.35)
    draw = ImageDraw.Draw(image)
    draw.text((960, 330), 'Physical AI 応用1講座 最終課題', font=font(34), fill=_SUBTEXT, anchor='mm')
    draw.text((960, 470), '文の指示で、色が同じで形の違う物体を', font=font(72, True), fill=_TEXT, anchor='mm')
    draw.text((960, 570), '見分けて仕分けるロボットアーム', font=font(72, True), fill=_TEXT, anchor='mm')
    draw.line((560, 660, 1360, 660), fill=_ACCENT, width=4)
    draw.text(
        (960, 730), 'CRANE+ V2 ・ ROS 2 Jazzy ・ Gazebo ・ 検出モデル OWLv2',
        font=font(36), fill=_TEXT, anchor='mm',
    )
    return image


def system_card() -> Image.Image:
    image, draw = card_canvas('しくみ: ROS 2 のノードの構成')
    boxes = {
        'text': ((120, 260, 520, 400), '指示の文', '「赤い直方体を左の箱へ」'),
        'client': ((700, 260, 1140, 400), 'send_instruction', '文から物体と箱の名前を取り出す'),
        'server': ((1340, 260, 1800, 400), 'pick_and_place_server', '掴む・置くを順に実行'),
        'camera': ((120, 560, 520, 700), 'カメラ（Gazebo）', '机を上から撮る'),
        'detector': ((700, 560, 1140, 700), 'object_detector', 'OWLv2 で物体を探す'),
        'arm': ((1340, 560, 1800, 700), 'MoveIt → CRANE+ V2', '腕の動かし方を計画して動かす'),
    }
    for (x0, y0, x1, y1), name, detail in boxes.values():
        draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=_PANEL, outline=_SUBTEXT, width=2)
        draw.text(((x0 + x1) // 2, y0 + 48), name, font=font(32, True), fill=_TEXT, anchor='mm')
        draw.text(((x0 + x1) // 2, y0 + 98), detail, font=font(24), fill=_SUBTEXT, anchor='mm')
    # 矢印の始点、終点、名前、名前を置く位置（右端の中央）
    arrows = [
        ((520, 330), (700, 330), '', None),
        ((1140, 330), (1340, 330), 'action', (1300, 300)),
        ((520, 630), (700, 630), 'topic（画像）', (690, 600)),
        ((1140, 590), (1340, 380), 'tf（物体の位置）', (1225, 470)),
        ((1570, 400), (1570, 560), '', None),
    ]
    for start, end, label, position in arrows:
        arrow(draw, start, end)
        if label:
            draw.text(position, label, font=font(26, True), fill=_ACCENT, anchor='rm')
    lines = [
        '物体を探すノードと、掴んで置くノードは、tf の「物体の位置」だけでつながる',
        '→ 物体の探し方を、色の範囲から AI に入れ替えても、掴む側は書き換えずに済んだ',
        '指示は自分で定義した action で送り、いまの段階（近づく・掴む・運ぶ…）を途中経過として受け取る',
    ]
    for k, line in enumerate(lines):
        draw.text((120, 800 + 60 * k), line, font=font(32), fill=_TEXT, anchor='lm')
    return image


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((*start, *end), fill=_ACCENT, width=5)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    tip = end
    left = (end[0] - 22 * ux + 12 * uy, end[1] - 22 * uy - 12 * ux)
    right = (end[0] - 22 * ux - 12 * uy, end[1] - 22 * uy + 12 * ux)
    draw.polygon([tip, left, right], fill=_ACCENT)


def ideas_card() -> Image.Image:
    image, draw = card_canvas('工夫: 色が同じ 2 個を見分ける')
    items = [
        ('文で物体を探す検出モデル OWLv2',
         '「red square block」「red long block」のような文で探す。3 つのモデルを比べて選んだ（学習はしていない）'),
        ('名前と枠を 1 対 1 に組み合わせる',
         '同じ名前の物体は 1 個だけ、という条件を使う。赤い 2 個の見分けが 40 個中 10 個 → 40 個に'),
        ('名前を付けた物体の位置を覚える',
         '箱に入れて見え方が変わっても、名前が入れ替わらない。運ばない限り、物体は動かないため'),
    ]
    for k, (head, detail) in enumerate(items):
        y = 280 + 210 * k
        draw.ellipse((120, y - 36, 192, y + 36), fill=_ACCENT)
        draw.text((156, y), str(k + 1), font=font(40, True), fill=(20, 20, 20), anchor='mm')
        draw.text((230, y - 22), head, font=font(44, True), fill=_TEXT, anchor='lm')
        draw.text((230, y + 42), detail, font=font(30), fill=_SUBTEXT, anchor='lm')
    return image


def results_card() -> Image.Image:
    image, draw = card_canvas('結果')
    rows = [
        ('検出モデルの比較', 'Gazebo の画像 20 枚の物体 80 個を、正しく見分けた数',
         'YOLO-World 54 ・ CLIP 78 ・ OWLv2 80'),
        ('仕分け', '比べるのに使っていない 11 通りの配置で、指示どおりの箱に入った物体',
         '44 個中 44 個'),
        ('処理を減らす工夫', '半精度、推論は 1 秒に 2 回まで、腕が動く間は推論しない',
         '仕分け中の CPU 3.1 → 0.4 コア分、GPU 67% → 3%'),
    ]
    for k, (head, detail, value) in enumerate(rows):
        y = 280 + 230 * k
        draw.text((120, y), head, font=font(40, True), fill=_ACCENT, anchor='lm')
        draw.text((120, y + 58), detail, font=font(28), fill=_SUBTEXT, anchor='lm')
        draw.text((120, y + 122), value, font=font(46, True), fill=_TEXT, anchor='lm')
    return image


def credits_card() -> Image.Image:
    image, draw = card_canvas('コードと使ったもの')
    draw.text((120, 280), 'github.com/siruku6/ros2-trial', font=font(56, True), fill=_TEXT, anchor='lm')
    lines = [
        'ROS 2 Jazzy ・ Gazebo Harmonic ・ MoveIt 2',
        'CRANE+ V2 の ROS 2 パッケージ: rt-net/crane_plus（Apache License 2.0）',
        '検出モデル: google/owlv2-base-patch16-ensemble（Apache-2.0）、PyTorch、Transformers',
    ]
    for k, line in enumerate(lines):
        draw.text((120, 430 + 70 * k), line, font=font(34), fill=_SUBTEXT, anchor='lm')
    return image


# ---- ffmpeg ----

def grab_frame(video: Path, time: float, output: Path) -> Path:
    _ffmpeg(['-ss', f'{time}', '-i', str(video), '-frames:v', '1', str(output)])
    return output


def encode_segment(video: Path, segment: Segment, output: Path) -> Path:
    """1 つの区切りを、同じ形式（1920 × 1080、30 fps、H.264）の MP4 にする。"""
    length = segment.duration / segment.speed
    if segment.card is not None:
        fade = f'fade=in:st=0:d=0.4,fade=out:st={length - 0.4:.3f}:d=0.4'
        inputs = ['-loop', '1', '-framerate', str(_FPS), '-t', f'{length:.3f}', '-i', str(segment.card)]
        graph = f'[0:v]{fade},format=yuv420p[v]'
    else:
        base = output.parent / 'screen_base.png'
        if not base.exists():
            screen_base().save(base)
        inputs = [
            '-ss', f'{segment.start:.3f}', '-t', f'{segment.duration:.3f}', '-i', str(video),
            '-loop', '1', '-i', str(base),
            '-loop', '1', '-i', str(segment.overlay),
        ]
        graph = (
            '[0:v]split=3[s1][s2][s3];'
            f'[s1]crop={_crop(_GAZEBO_CROP)}[gz];'
            f'[s2]crop={_crop(_DETECTION_CROP)}[det];'
            f'[s3]crop={_crop(_TERMINAL_CROP)}[term];'
            f'[1:v][gz]overlay={_GAZEBO_AT[0]}:{_GAZEBO_AT[1]}:shortest=1[a];'
            f'[a][det]overlay={_DETECTION_AT[0]}:{_DETECTION_AT[1]}[b];'
            f'[b][term]overlay={_TERMINAL_AT[0]}:{_TERMINAL_AT[1]}[c];'
            '[c][2:v]overlay=0:0[d];'
            f'[d]setpts=(PTS-STARTPTS)/{segment.speed},fps={_FPS},format=yuv420p[v]'
        )
    _ffmpeg(inputs + [
        '-filter_complex', graph, '-map', '[v]', '-t', f'{length:.3f}',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', str(_FPS),
        '-video_track_timescale', '15360', str(output),
    ])
    return output


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
