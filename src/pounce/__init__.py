"""
Pounce — A free-threading-native ASGI server for Python 3.14t.

Pounce is a pure-Python ASGI server designed from scratch for Python's free-threading
mode (PEP 703). Instead of the traditional fork-based worker model, pounce runs N worker
threads sharing a single interpreter — leveraging nogil for true parallelism without the
memory overhead of multi-process deployments.

Quick start:

    import pounce

    pounce.run("myapp:app", host="0.0.0.0", port=8000)

Or from the command line:

    pounce myapp:app --workers 4

Part of the Bengal ecosystem:

    pounce      ASGI server       (serves apps)
    chirp       Web framework     (serves HTML)
    kida        Template engine   (renders HTML)
    patitas     Markdown parser   (parses content)
    rosettes    Syntax highlighter (highlights code)
    bengal      Static site gen   (builds sites)

"""

# PEP 703: Declare this module as free-threading safe
_Py_mod_gil = 0

__version__ = "0.1.0-dev"
