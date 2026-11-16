#!/bin/sh
# The simulated launch behind docs/diagrams/launch-*.html: `frontier mission start` for provider openai with the real
# launcher and the real Codex recipe of config/harnesses.toml; only the `codex` binary is replaced by ./codex. The trace
# hook (./sitecustomize.py) records every frontier/ call, file and subprocess of every process to OUT/trace.jsonl.
# It spends real money: three tiny OpenAI calls (0.0026 USD on 2026-11-16), and it reads Scaleway and Runpod offers.
#   docs/diagrams/launch-sim/run.sh [OUT]        OUT defaults to /tmp/frontier-launch-sim
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=${1:-/tmp/frontier-launch-sim}
rm -rf "$OUT"
mkdir -p "$OUT/bin" "$OUT/subject"
cp "$HERE/mission.toml" "$OUT/"
cp "$HERE/subject.md" "$OUT/subject/"
cp "$HERE/codex" "$OUT/bin/"
chmod +x "$OUT/bin/codex"
export FRONTIER_TRACE="$OUT/trace.jsonl" TRACE_ROOT="$ROOT" TRACE_WATCH="$OUT/ws:$OUT/state" TRACE_PROC=launcher
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" PATH="$OUT/bin:$PATH"
cd "$ROOT"
python3 -m frontier mission start "$OUT/mission.toml"
python3 -m frontier mission status "$OUT/mission.toml"
python3 "$HERE/summarize.py" "$OUT/trace.jsonl" 6
