#!/bin/sh
set -eu

: "${PORT:?Railway must provide PORT}"

/opt/venv/bin/python -c 'import sys; assert not sys._is_gil_enabled(), "Pounce Railway recipe requires CPython 3.14t with the GIL disabled"'

exec /opt/venv/bin/python /app/app.py
