# Pounce

Modern ASGI server for free-threaded Python 3.14t+. Pure Python, no C extensions.

## Quick Reference

```bash
make setup          # Create venv with Python 3.14t
make install        # Install in editable mode (uv sync --group dev)
make test           # Full test suite
make lint           # Ruff linter
make format         # Ruff formatter
make ty             # ty type checker
make build          # Build distribution
make gh-release     # GitHub release (triggers PyPI)
```

## Project Structure

```
src/pounce/           # Main source (~58 files)
  server.py           # Top-level orchestrator, lifecycle state machine
  supervisor.py       # Worker spawning, health monitoring, auto-restart
  worker.py           # Main async loop, connection/request handling
  config.py           # Frozen ServerConfig dataclass (50+ options)
  asgi/               # ASGI bridge (scope construction, streaming send/receive)
  protocols/          # Sans-I/O protocol handlers (H1, H2, WebSocket, H3)
  _compression.py     # zstd/gzip content negotiation
  _middleware.py       # CORS, security headers
  _static.py          # Static file serving with ETags
  _cli.py             # CLI (argparse)
tests/unit/           # ~53 unit test files
tests/integration/    # ~15 integration test files
examples/             # 17 example apps
docs/                 # Architecture docs, feature specs, deployment guides
benchmarks/           # Performance benchmarks
```

## Architecture

Request pipeline: Socket → Protocol Parser (H1/H2/WS/H3) → ASGI Bridge → App → Response → Compression → Socket Write.

Worker model auto-detects GIL state via `sys._is_gil_enabled()`:
- **3.14t (nogil):** spawns worker threads (shared interpreter)
- **3.14 (GIL):** spawns worker processes (fork)

## Code Style

- **Formatter/Linter:** ruff (line length 100, target py314)
- **Type checker:** ty (Rust-based, strict)
- **Rules:** E, W, F, UP, B, SIM, I, N, PIE, PERF, C4, RUF, PT
- Zero `type: ignore` comments target

## Testing

```bash
pytest tests/ -x -q --timeout=10    # Fast feedback
pytest tests/unit/                   # Unit only
pytest tests/ --cov=pounce           # With coverage (threshold: 80%)
pytest tests/ -m benchmark           # Benchmarks
```

- asyncio_mode: auto
- Markers: slow, integration, benchmark, timeout
- Key fixture: `@with_lifespan` decorator (conftest.py)

## Dependencies

- **Runtime:** h11 (required); h2, wsproto, truststore, bengal-zoomies (optional)
- **Dev:** pytest, pytest-asyncio, ruff, ty, httpx, hypothesis, pre-commit
- **Package manager:** uv
