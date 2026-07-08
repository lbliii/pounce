#!/bin/sh
set -eu

: "${PORT:?Railway must provide PORT}"

python -c 'import sys; assert not sys._is_gil_enabled(), "Pounce Railway recipe requires CPython 3.14t with the GIL disabled"'

exec python /app/app.py
