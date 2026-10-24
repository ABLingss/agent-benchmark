#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
for candidate in "${BENCHMARK_PYTHON:-}" /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$candidate" -m submission.server "$@"
  fi
done
echo "需要 Python 3.10 或更新版本。安装后重新双击本文件。"
read -r -p "按回车关闭…"
exit 1
