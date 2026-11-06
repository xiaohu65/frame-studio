"""Retired private-project bootstrap; new workspaces start from the web UI."""
import sys


MESSAGE = "旧版私有素材预置入口已停用。请启动帧工坊，在网页中新建项目并导入自己的素材。"


def seed():
    """Reject the old entry point before importing storage or creating data."""
    raise RuntimeError(MESSAGE)


def main():
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
