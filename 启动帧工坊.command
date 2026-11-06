#!/bin/zsh
set -eu
cd "${0:A:h}"
FRAME_PYTHON="${FRAME_STUDIO_PYTHON:-/Users/xiaohu/.local/share/blender-whitebox/analysis-env/bin/python}"
if [[ ! -x "$FRAME_PYTHON" ]]; then
  echo "未找到 Python：$FRAME_PYTHON"
  echo '请在 README「启动、更新与故障诊断」中核对环境，或设置 FRAME_STUDIO_PYTHON 后重试。'
  exit 1
fi
exec "$FRAME_PYTHON" scripts/frame_studio_launcher.py "$@"
