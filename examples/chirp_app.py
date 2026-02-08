"""
Chirp framework integration — a minimal chirp app served by pounce.

Chirp is pounce's companion web framework (part of the Bengal ecosystem).
This example shows that any chirp ``App`` works as an ASGI callable and
can be served by pounce without modification.

Prerequisites:
    pip install chirp   # or: uv add chirp

Run it:
    pounce examples.chirp_app:app

Then visit http://127.0.0.1:8000/ in a browser.

"""

try:
    import chirp
except ImportError as exc:
    raise ImportError("This example requires chirp.  Install it with: pip install chirp") from exc

# ---------------------------------------------------------------------------
# Build a minimal chirp application
# ---------------------------------------------------------------------------

app = chirp.App()


@app.route("/")
def index() -> str:
    """Home page."""
    return "Hello from chirp, served by pounce!"


@app.route("/health")
def health() -> dict[str, str]:
    """Health check endpoint returning JSON."""
    return {"status": "ok", "server": "pounce", "framework": "chirp"}
