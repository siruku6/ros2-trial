"""録画のために、noVNC の画面の窓を、決まった位置と大きさに並べる。

コンテナの中で、システムの Python で実行する（apt の python3-xlib を使う）。
窓は、題名の一部と「x,y,幅,高さ」で指定する。指定した順に手前に出す。

    DISPLAY=:1 python3 tools/video/arrange_windows.py 'Gazebo Sim=0,0,1700,1050'
    DISPLAY=:1 python3 tools/video/arrange_windows.py --list
"""

import argparse
import time

from Xlib import X, display
from Xlib.protocol import event

# _NET_MOVERESIZE_WINDOW の flags: 重力は窓の左上、x・y・幅・高さのすべてを指定する
_MOVERESIZE_FLAGS = 1 | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11)
# _NET_ACTIVE_WINDOW の送り主の種類: ページャなど（ユーザの操作として扱われる）
_SOURCE_PAGER = 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('placements', nargs='*', help="'題名の一部=x,y,幅,高さ'")
    parser.add_argument('--list', action='store_true', help='窓の題名と位置を表示する')
    args = parser.parse_args()
    screen = display.Display()
    windows = _client_windows(screen)
    if args.list:
        for window in windows:
            geometry = _frame_geometry(window)
            print(f'{_title(screen, window)!r} {geometry}')
        return
    for placement in args.placements:
        title_part, numbers = placement.rsplit('=', 1)
        x, y, width, height = (int(value) for value in numbers.split(','))
        matches = [window for window in windows if title_part in _title(screen, window)]
        if not matches:
            raise SystemExit(f'題名に「{title_part}」を含む窓がありません')
        for window in matches:
            _move_resize(screen, window, x, y, width, height)
            _activate(screen, window)
    screen.sync()
    time.sleep(0.5)


def _client_windows(screen: display.Display) -> list:
    """ウィンドウマネージャが管理している窓の一覧を返す。"""
    root = screen.screen().root
    client_list = root.get_full_property(screen.intern_atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
    return [screen.create_resource_object('window', wid) for wid in client_list.value]


def _title(screen: display.Display, window) -> str:
    name = window.get_full_property(screen.intern_atom('_NET_WM_NAME'), 0)
    if name is not None:
        value = name.value
        return value.decode() if isinstance(value, bytes) else str(value)
    return window.get_wm_name() or ''


def _frame_geometry(window) -> tuple[int, int, int, int]:
    geometry = window.get_geometry()
    position = window.translate_coords(window.query_tree().root, 0, 0)
    return (-position.x, -position.y, geometry.width, geometry.height)


def _send_root_message(screen: display.Display, window, message_type: str, data: list) -> None:
    root = screen.screen().root
    message = event.ClientMessage(
        window=window,
        client_type=screen.intern_atom(message_type),
        data=(32, data + [0] * (5 - len(data))),
    )
    root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)


def _move_resize(screen: display.Display, window, x: int, y: int, width: int, height: int) -> None:
    # 最大化されていると位置を変えられないので、先に解く
    state = screen.intern_atom('_NET_WM_STATE')
    maximized = [
        screen.intern_atom('_NET_WM_STATE_MAXIMIZED_VERT'),
        screen.intern_atom('_NET_WM_STATE_MAXIMIZED_HORZ'),
    ]
    root = screen.screen().root
    root.send_event(
        event.ClientMessage(
            window=window, client_type=state, data=(32, [0, maximized[0], maximized[1], 1, 0])
        ),
        event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
    )
    _send_root_message(
        screen, window, '_NET_MOVERESIZE_WINDOW', [_MOVERESIZE_FLAGS, x, y, width, height]
    )


def _activate(screen: display.Display, window) -> None:
    _send_root_message(screen, window, '_NET_ACTIVE_WINDOW', [_SOURCE_PAGER, X.CurrentTime])


if __name__ == '__main__':
    main()
