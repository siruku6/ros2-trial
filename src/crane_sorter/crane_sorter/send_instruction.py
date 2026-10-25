"""文の指示を、物体を掴んで箱に置く action の依頼にして、順に送るコマンド。

使い方:
    ros2 run crane_sorter send_instruction "赤いブロックを左の箱へ" "青いブロックを右の箱へ"

実行中に Ctrl+C を押すと、実行中の依頼の取り消しを求めてから終わる。
"""

import sys

from action_msgs.msg import GoalStatus
from crane_sorter.instruction import parse_instruction
from crane_sorter_interfaces.action import PickAndPlace
import rclpy
from rclpy.action import ActionClient
from rclpy.action.client import ClientGoalHandle
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from rclpy.task import Future

_SERVER_WAIT_SECONDS = 30.0
# 結果を待つ間に、Ctrl+C を確かめる間隔 [s]
_SPIN_PERIOD_SECONDS = 0.1
# 依頼の終わり方（action の状態）ごとの表示
_OUTCOME_LABELS: dict[int, str] = {
    GoalStatus.STATUS_SUCCEEDED: '成功',
    GoalStatus.STATUS_CANCELED: '取り消し',
    GoalStatus.STATUS_ABORTED: '失敗',
}


def main(args: list[str] | None = None) -> None:
    # 出力をパイプやファイルに流したときも、途中経過が 1 行ずつすぐ見えるようにする
    sys.stdout.reconfigure(line_buffering=True)
    # Ctrl+C を自分で受けて取り消しを送るため、rclpy のシグナル処理は使わない
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    instructions = rclpy.utilities.remove_ros_args(sys.argv)[1:]
    if not instructions:
        print(__doc__)
        sys.exit(2)

    node = Node('send_instruction')
    try:
        exit_code = _send_all(node, instructions)
    except KeyboardInterrupt:
        # 実行中の依頼があれば、取り消しを済ませてからここに来る。残りの指示は送らない
        print('中断しました')
        exit_code = 130
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    sys.exit(exit_code)


def _send_all(node: Node, instructions: list[str]) -> int:
    """指示を順に依頼として送り、全部成功したら 0、どれかが失敗したら 1 を返す。"""
    client = ActionClient(node, PickAndPlace, 'pick_and_place')
    if not client.wait_for_server(timeout_sec=_SERVER_WAIT_SECONDS):
        print('action サーバ /pick_and_place が見つかりません。sorter.launch.py を起動してください')
        return 1

    all_succeeded = True
    for index, instruction in enumerate(instructions, start=1):
        progress_label = f'[{index}/{len(instructions)}]'
        succeeded = _run_one_instruction(node, client, instruction, progress_label)
        all_succeeded = all_succeeded and succeeded
    return 0 if all_succeeded else 1


def _run_one_instruction(
    node: Node, client: ActionClient, instruction: str, progress_label: str
) -> bool:
    """指示を 1 つ依頼として送り、結果を待って表示する。成功したら True を返す。

    結果を待つ間に Ctrl+C を押されたら、取り消しを求めて結果を表示してから、
    KeyboardInterrupt を投げ直して残りの指示を止める。
    """
    try:
        object_label, box_name = parse_instruction(instruction)
    except ValueError as error:
        print(f'{progress_label} {error}')
        return False
    print(f'{progress_label} 「{instruction}」→ 物体 {object_label} を箱 {box_name} へ')

    goal = PickAndPlace.Goal(object_label=object_label, box_name=box_name)
    send_future = client.send_goal_async(
        goal,
        feedback_callback=lambda message: print(
            f'{progress_label}   段階: {message.feedback.stage}'
        ),
    )
    _spin_until_done(node, send_future)
    goal_handle: ClientGoalHandle = send_future.result()
    if not goal_handle.accepted:
        print(f'{progress_label} 断られました（ほかの依頼を実行中です）')
        return False

    result_future = goal_handle.get_result_async()
    interrupted = False
    try:
        _spin_until_done(node, result_future)
    except KeyboardInterrupt:
        interrupted = True
        _cancel(node, goal_handle, progress_label)
        _spin_until_done(node, result_future)

    response = result_future.result()
    result: PickAndPlace.Result = response.result
    outcome = _OUTCOME_LABELS.get(response.status, '失敗')
    print(f'{progress_label} {outcome}: {result.message}')
    if interrupted:
        raise KeyboardInterrupt
    return bool(result.success)


def _cancel(node: Node, goal_handle: ClientGoalHandle, progress_label: str) -> None:
    """実行中の依頼の取り消しを求める。"""
    print(f'{progress_label} 取り消しを求めます')
    cancel_future = goal_handle.cancel_goal_async()
    _spin_until_done(node, cancel_future)


def _spin_until_done(node: Node, future: Future) -> None:
    """future が終わるまで、ノードに届くメッセージを処理しながら待つ。

    rclpy.spin_until_future_complete を時間の区切りなしで使うと、次のメッセージが届くまで
    C の関数の中で待ち続けるので、その間に押した Ctrl+C が数秒遅れて効く。
    短い区切りで待ちを繰り返し、Ctrl+C がすぐ KeyboardInterrupt になるようにする。
    """
    while not future.done():
        rclpy.spin_once(node, timeout_sec=_SPIN_PERIOD_SECONDS)
