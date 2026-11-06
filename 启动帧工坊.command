#!/bin/zsh
set -eu
cd "${0:A:h}"

frame_python_error() {
  print -u2 -- "启动未完成：$1"
  print -u2 -- '请按 README「首次使用」准备 Python 3.12+ 独立环境，或通过 FRAME_STUDIO_PYTHON 指定可用解释器。'
  exit 1
}

if [[ -n "${FRAME_STUDIO_PYTHON:-}" ]]; then
  FRAME_PYTHON="$FRAME_STUDIO_PYTHON"
elif [[ -e "$PWD/.venv/bin/python" || -L "$PWD/.venv/bin/python" ]]; then
  FRAME_PYTHON="$PWD/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  FRAME_PYTHON="$VIRTUAL_ENV/bin/python"
else
  FRAME_PYTHON="python3"
fi

if [[ "$FRAME_PYTHON" != */* ]]; then
  FRAME_PYTHON_PATH="$(whence -p -- "$FRAME_PYTHON" 2>/dev/null)" || frame_python_error "未找到 Python 命令：$FRAME_PYTHON"
  FRAME_PYTHON="$FRAME_PYTHON_PATH"
fi
if [[ ! -x "$FRAME_PYTHON" || -d "$FRAME_PYTHON" ]]; then
  frame_python_error "未找到可执行的 Python：$FRAME_PYTHON"
fi
if ! "$FRAME_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
  frame_python_error "Python 不可用或版本低于 3.12：$FRAME_PYTHON"
fi
exec "$FRAME_PYTHON" scripts/frame_studio_launcher.py "$@"
