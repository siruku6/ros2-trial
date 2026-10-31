#!/bin/bash
# 録画の合図を待ってから、文の指示を送る。record_demo.sh が、画面の端末（xterm）の中で実行する。
# 出力は端末に出しつつ、行ごとに時刻を付けて記録する。編集のとき、等速と 2 倍速の区切りに使う。
#
#   send_and_log.sh <記録のファイル> <合図のファイル> <指示> [<指示> ...]

log_file=$1
go_file=$2
shift 2

while [ ! -e "$go_file" ]; do
    sleep 0.2
done

printf '$ ros2 run crane_sorter send_instruction'
printf ' "%s"' "$@"
printf '\n'
# 端末の画面へは行ごとにすぐ出るよう、Python の出力をためずに流す
PYTHONUNBUFFERED=1 ros2 run crane_sorter send_instruction "$@" 2>&1 |
    while IFS= read -r line; do
        printf '%s\n' "$line"
        printf '%s %s\n' "$(date +%s.%N)" "$line" >> "$log_file"
    done
printf '%s %s\n' "$(date +%s.%N)" 'done' >> "$log_file"
sleep infinity
