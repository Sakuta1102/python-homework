#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> git pull"
git pull --ff-only

echo "==> systemctl restart data-cleaner"
systemctl restart data-cleaner

echo "==> 等待 3s 让服务稳定"
sleep 3

echo "==> systemctl status (最近 20 行日志)"
systemctl status data-cleaner --no-pager -n 20
