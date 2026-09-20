"""`st` —— ROS 風格的站台命令列(README 5.1)。

```
st node list
st service list
st service call     PFR-100L/COM20 "set_voltage 12"
st service describe PFR-100L/COM20
st topic list
st topic echo BTM-4208SD/COM7/ch3
st topic pub  test/fake/ch1 '{"v": 1.0}'
st topic hz   BTM-4208SD/COM7/ch3
st launch     launch/burn_in_station.yaml
st console    Serial/COM5
st                                   # 互動模式(REPL)
```
"""

from __future__ import annotations

import argparse
import sys

from . import commands
from .common import session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="st", description="test-station CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不給任何參數則進入互動模式(REPL),Tab 補完來自 describe。",
    )
    groups = parser.add_subparsers(dest="group")

    node = groups.add_parser("node", help="node 查詢").add_subparsers(dest="action")
    node.add_parser("list", help="列出站上的 node 與燈號")

    service = groups.add_parser("service", help="service 查詢與呼叫")
    service_actions = service.add_subparsers(dest="action")
    service_actions.add_parser("list", help="列出所有 service")
    call = service_actions.add_parser("call", help="送一行純文字命令")
    call.add_argument("service", help="例如 BTM-4208SD/COM11")
    call.add_argument("command", nargs="+", help='例如 "set_voltage 12"')
    described = service_actions.add_parser("describe", help="列出命令清單")
    described.add_argument("service")

    topic = groups.add_parser("topic", help="topic 查詢與收發")
    topic_actions = topic.add_subparsers(dest="action")
    topic_actions.add_parser("list", help="列出所有被發佈的 topic")
    echo = topic_actions.add_parser("echo", help="印出 topic 的每一筆")
    echo.add_argument("topic")
    echo.add_argument("-n", "--count", type=int, help="收滿幾筆就停")
    publish = topic_actions.add_parser("pub", help="發佈到任意 topic")
    publish.add_argument("topic")
    publish.add_argument("payload", help='JSON,例如 \'{"v": 1.0}\'')
    publish.add_argument("-r", "--rate", type=float, help="重複發佈的頻率 (Hz)")
    publish.add_argument("-n", "--count", type=int, help="搭配 --rate,發幾筆就停")
    hz = topic_actions.add_parser("hz", help="量測發佈頻率")
    hz.add_argument("topic")
    hz.add_argument("-w", "--window", type=float, default=1.0, help="統計窗長(秒)")

    console = groups.add_parser("console", help="DUT 雙窗格 TUI")
    console.add_argument("target", help="例如 Serial/COM5")

    launch = groups.add_parser("launch", help="依序啟動整站")
    launch.add_argument("config", help="例如 launch/burn_in_station.yaml")

    return parser


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """把已解析的參數送到對應的實作。REPL 與一次性呼叫共用這裡。"""
    if args.group == "node":
        if args.action == "list":
            return commands.node_list()
    elif args.group == "service":
        if args.action == "list":
            return commands.service_list()
        if args.action == "call":
            return commands.service_call(args.service, " ".join(args.command))
        if args.action == "describe":
            return commands.service_describe(args.service)
    elif args.group == "topic":
        if args.action == "list":
            return commands.topic_list()
        if args.action == "echo":
            return commands.topic_echo(args.topic, args.count)
        if args.action == "pub":
            return commands.topic_pub(args.topic, args.payload, args.rate, args.count)
        if args.action == "hz":
            return commands.topic_hz(args.topic, args.window)
    elif args.group == "console":
        from .console import run_console
        return run_console(args.target)
    elif args.group == "launch":
        from .launch import run_launch
        return run_launch(args.config)

    parser.parse_args([args.group, "--help"] if args.group else ["--help"])
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        from .repl import run_repl
        with session("st"):
            return run_repl(parser)

    args = parser.parse_args(argv)
    with session("st"):
        return dispatch(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
