#!/bin/bash
set -e
APPROOT=$(python3 -c "import os; print([p for p in os.environ.get('PYTHONPATH','').split(':') if 'antenv/lib' in p][0].split('/antenv/lib')[0])")
export PYTHONPATH="$PYTHONPATH:$APPROOT"
cd "$APPROOT"
exec python3 -m gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --timeout 180
