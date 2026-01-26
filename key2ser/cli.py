from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from key2ser.config import DEFAULT_CONFIG_PATH, load_config


# CLIの引数パーサを組み立てる。
def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の構造を定義したパーサを返す。"""
    parser = argparse.ArgumentParser(description="HID入力を仮想シリアルへ送信します。")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="設定ファイルパス (default: config.ini)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="ログレベル (DEBUG, INFO, WARNING)",
    )
    return parser


# サポート外プラットフォーム向けの警告文を生成する。
def _unsupported_platform_message(platform: str) -> str | None:
    """実行環境が非対応の場合に表示するメッセージを返す。"""
    if platform.startswith("win"):
        return "Windows では対応していません。Linux (evdev) 環境で実行してください。"
    return None


# ログレベルの指定を検証して実際のレベル値に変換する。
def _resolve_log_level(value: str) -> tuple[int, str | None]:
    """ログレベルを解決し、必要なら警告メッセージを返す。"""
    normalized = str(value).upper()
    resolved = logging.getLevelName(normalized)
    if isinstance(resolved, int):
        return resolved, None
    return logging.INFO, f"未対応のログレベル {value} が指定されたため INFO にフォールバックします。"


# エントリポイントとして設定読み込みとイベントループを起動する。
def main(argv: list[str] | None = None) -> int:
    """CLI起動時の設定読み込みと実行処理を行う。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_level, warning_message = _resolve_log_level(args.log_level)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if warning_message is not None:
        logging.warning("%s", warning_message)

    # 非対応プラットフォームでは実行を止めて明示的に終了する。
    platform_message = _unsupported_platform_message(sys.platform)
    if platform_message is not None:
        logging.error("%s", platform_message)
        return 1

    from key2ser import runner

    try:
        config = load_config(args.config)
        runner.run_event_loop(config)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return 2
    except runner.DeviceNotFoundError as exc:
        logging.error("%s", exc)
        return 3
    except runner.DeviceAccessError as exc:
        logging.error("%s", exc)
        return 3
    except runner.SerialConnectionError as exc:
        logging.error("%s", exc)
        return 5    
    except ValueError as exc:
        logging.error("%s", exc)
        return 4
    except KeyboardInterrupt:
        logging.info("終了します。")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
