#!/bin/bash
# 録画の合図を待ってから、文の指示を 1 つずつ送る。record_demo.sh が、画面の端末（xterm）の中で
# 実行する。出力は端末に出しつつ、行ごとに時刻を付けて記録する。編集のとき、字幕の区切りに使う。
# 指示が失敗したら（腕が動かないので）、端末の答えを読めるよう、しばらく待ってから次を送る。
#
#   send_and_log.sh <記録のファイル> <合図のファイル> <指示> [<指示> ...]

log_file=$1
go_file=$2
shift 2

while [ ! -e "$go_file" ]; do
    sleep 0.2
done

# 指示を送り終えたあとに待つ時間 [s]。成功したときと、失敗したとき
pause_after_success=1
pause_after_failure=8

for instruction in "$@"; do
    printf '$ ros2 run crane_sorter send_instruction "%s"\n' "$instruction"
    # 端末の画面へは行ごとにすぐ出るよう、Python の出力をためずに流す。
    # 失敗したときに ros2 run が足す行（Process exited with failure）は、失敗の行と重なるので出さない
    PYTHONUNBUFFERED=1 ros2 run crane_sorter send_instruction "$instruction" 2>&1 |
        while IFS= read -r line; do
            [[ $line == '[ros2run]: Process exited with failure'* ]] && continue
            printf '%s\n' "$line"
            printf '%s %s\n' "$(date +%s.%N)" "$line" >> "$log_file"
        done
    if [ "${PIPESTATUS[0]}" -eq 0 ]; then
        sleep "$pause_after_success"
    else
        sleep "$pause_after_failure"
    fi
done
printf '%s %s\n' "$(date +%s.%N)" 'done' >> "$log_file"
sleep infinity
