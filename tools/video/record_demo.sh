#!/bin/bash
# 動画の実演の場面を録画する。
# Gazebo と AI 版のノードを起動し、窓を並べ、画面を録画しながら 4 つの指示を送る。
# コンテナの中で実行する。録画と記録は出力先のディレクトリに置く。
#
#   bash /ws/tools/video/record_demo.sh [出力先（既定 /ws/tmp/video/take）] [乱数の種（既定 24）]
#
# 出力:
#   screen.mkv        画面全体の録画（1920 × 1080、30 fps）
#   record_start.txt  録画を始めた時刻（UNIX 時刻）
#   instructions.log  指示を送る端末の出力に、行ごとの時刻を付けたもの

set -eu
set -m

out_dir=${1:-/ws/tmp/video/take}
seed=${2:-24}
instructions=('赤い直方体を左の箱へ' '赤いブロックを右の箱へ' '青いブロックを左の箱へ' '緑の直方体を右の箱へ')
tools_dir=$(cd "$(dirname "$0")" && pwd)
export DISPLAY=:1

# 指示を送る前に、机の上を見せる時間 [s]
intro_seconds=15
# 指示がすべて終わるまで待つ上限 [s]
instruction_timeout=360

mkdir -p "$out_dir"
rm -f "$out_dir"/{screen.mkv,record_start.txt,instructions.log,go}

wait_for_log() {
    local file=$1 pattern=$2 timeout=$3
    for _ in $(seq "$timeout"); do
        grep -aq "$pattern" "$file" 2> /dev/null && return 0
        sleep 1
    done
    echo "待ちきれませんでした: $file に「$pattern」が出ない" >&2
    return 1
}

stop_all() {
    [ -n "${recorder:-}" ] && kill -INT "$recorder" 2> /dev/null && wait "$recorder" || true
    pkill -f '[s]end_and_log.sh' || true
    pkill -f '[x]term -T instruction' || true
    pkill -9 -f '[r]qt_image_view/rqt_image_view' || true
    [ -n "${sorter:-}" ] && kill -INT -- "-$sorter" 2> /dev/null || true
    [ -n "${sim:-}" ] && kill -INT -- "-$sim" 2> /dev/null || true
    sleep 8
    pkill -f '[g]z sim' || true
    pkill -f '[m]ove_group' || true
    pkill -f '[r]viz2' || true
}
trap stop_all EXIT

# 録画の途中で画面が黒くならないよう、スクリーンセーバーを止める
pkill -f '[m]ate-screensaver' || true

ros2 launch crane_sorter sim.launch.py \
    layout:=/ws/src/crane_sorter/config/layout_shapes.yaml seed:="$seed" > "$out_dir/sim.log" 2>&1 &
sim=$!
wait_for_log "$out_dir/sim.log" 'Configured and activated crane_plus_arm_controller' 120
sleep 5
ros2 launch crane_sorter sorter.launch.py detector:=ai > "$out_dir/sorter.log" 2>&1 &
sorter=$!
wait_for_log "$out_dir/sorter.log" '依頼を待っています' 180
wait_for_log "$out_dir/sorter.log" '用意しました' 180

# RViz は crane_plus の launch が必ず開くので、閉じる
pkill -f '[r]viz2' || true
ros2 run rqt_image_view rqt_image_view /object_detector/detections > "$out_dir/rqt.log" 2>&1 &
# 日本語を出すので UTF-8 で起動する。半角と全角でフォントを分け、英数字の間が空かないようにする
LANG=C.UTF-8 xterm -u8 -T instruction -fa 'DejaVu Sans Mono' -fd 'Noto Sans Mono CJK JP' -fs 13 \
    -bg black -fg white \
    -e bash "$tools_dir/send_and_log.sh" "$out_dir/instructions.log" "$out_dir/go" "${instructions[@]}" &
sleep 6

# 窓の配置。編集（edit_video.py）は、この配置を前提に画面を切り出す
python3 "$tools_dir/arrange_windows.py" \
    'Gazebo Sim=0,28,1700,1024' \
    'rqt_image_view=1280,28,640,700' \
    'instruction=1280,650,640,402'
# Gazebo の視点: 腕の正面から、腕の全体と机の上の物体・箱が入る角度
python3 "$tools_dir/set_gz_camera.py" 0.88 0.0 1.50 0.06 0.0 1.13 > /dev/null
# マウスの矢印は、説明の出ない上端のパネルの何もない所に置く
python3 -c "from Xlib import display; d = display.Display(); d.screen().root.warp_pointer(700, 14); d.sync()"
sleep 3

date +%s.%N > "$out_dir/record_start.txt"
ffmpeg -hide_banner -loglevel error -y -f x11grab -draw_mouse 0 -framerate 30 -video_size 1920x1080 -i :1 \
    -c:v h264_nvenc -preset p5 -cq 20 -pix_fmt yuv420p "$out_dir/screen.mkv" < /dev/null &
recorder=$!

sleep "$intro_seconds"
touch "$out_dir/go"
wait_for_log "$out_dir/instructions.log" '^[0-9.]* done$' "$instruction_timeout"
sleep 4
echo "録画しました: $out_dir/screen.mkv"
grep -a -E '成功|失敗|取り消し|断られ' "$out_dir/instructions.log" || true
