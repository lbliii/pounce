"""
Rate limiting demonstration.

Shows how pounce's token bucket rate limiter works in practice.
Each client IP gets its own bucket with configurable rate and burst.

Run:
    python examples/rate_limiting_demo.py

Test rate limiting:
    # Burst test - send 5 rapid requests (burst allows)
    for i in {1..5}; do curl http://localhost:8000/api; done

    # Sustained test - send 20 rapid requests (rate limited after burst)
    for i in {1..20}; do curl http://localhost:8000/api; sleep 0.1; done

    # View metrics to see rate limiting in action
    curl http://localhost:8000/metrics | grep http_requests_total

"""

import time

from pounce import run, ServerConfig


async def app(scope, receive, send):
    """Simple API that demonstrates rate limiting."""
    if scope["type"] != "http":
        return

    path = scope["path"]

    # API endpoint
    if path == "/api":
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-ratelimit-limit", b"5"),  # 5 req/s
                (b"x-ratelimit-burst", b"10"),  # Burst of 10
            ],
        })
        await send({
            "type": "http.response.body",
            "body": (
                b'{"message": "Request successful!", '
                b'"timestamp": ' + str(int(time.time())).encode() + b', '
                b'"tip": "Try sending many rapid requests to see rate limiting"}'
            ),
        })
        return

    # Status page
    if path == "/" or path == "/status":
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/html")],
        })
        await send({
            "type": "http.response.body",
            "body": b"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Rate Limiting Demo</title>
                <style>
                    body { font-family: system-ui; max-width: 800px; margin: 50px auto; padding: 20px; }
                    code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
                    pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }
                    .success { color: green; }
                    .error { color: red; }
                    button { padding: 10px 20px; font-size: 16px; cursor: pointer; }
                    #results { margin-top: 20px; }
                </style>
            </head>
            <body>
                <h1>Rate Limiting Demo</h1>
                <p>This server is configured with:</p>
                <ul>
                    <li><strong>Rate:</strong> 5 requests/second per IP</li>
                    <li><strong>Burst:</strong> 10 requests (initial capacity)</li>
                </ul>

                <h2>Test Rate Limiting</h2>
                <button onclick="sendRequests(5)">Send 5 Requests (Should Succeed)</button>
                <button onclick="sendRequests(20)">Send 20 Requests (Will Hit Limit)</button>

                <div id="results"></div>

                <h2>How It Works</h2>
                <p>Token bucket algorithm:</p>
                <ol>
                    <li>Each client IP gets a bucket with 10 initial tokens</li>
                    <li>Tokens refill at 5 per second</li>
                    <li>Each request consumes 1 token</li>
                    <li>When empty, requests get <code>429 Too Many Requests</code></li>
                </ol>

                <h2>View Metrics</h2>
                <p>Check Prometheus metrics to see rate limiting in action:</p>
                <pre>curl http://localhost:8000/metrics | grep http_requests_total</pre>

                <script>
                    async function sendRequests(count) {
                        const results = document.getElementById('results');
                        results.innerHTML = '<h3>Sending ' + count + ' requests...</h3>';

                        let success = 0;
                        let ratelimited = 0;

                        for (let i = 0; i < count; i++) {
                            try {
                                const response = await fetch('/api');
                                if (response.status === 200) {
                                    success++;
                                } else if (response.status === 429) {
                                    ratelimited++;
                                }
                            } catch (e) {
                                console.error(e);
                            }
                            // Small delay to see individual requests
                            await new Promise(r => setTimeout(r, 50));
                        }

                        results.innerHTML += '<p class="success">✓ ' + success + ' requests succeeded</p>';
                        if (ratelimited > 0) {
                            results.innerHTML += '<p class="error">✗ ' + ratelimited + ' requests rate limited (429)</p>';
                        }
                    }
                </script>
            </body>
            </html>
            """,
        })
        return

    # 404 for other paths
    await send({
        "type": "http.response.start",
        "status": 404,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({
        "type": "http.response.body",
        "body": b"Not Found",
    })


if __name__ == "__main__":
    config = ServerConfig(
        host="127.0.0.1",
        port=8000,
        workers=2,

        # Enable rate limiting with low limits for demo
        rate_limit_enabled=True,
        rate_limit_requests_per_second=5.0,  # 5 req/s per IP
        rate_limit_burst=10,  # Allow bursts up to 10

        # Enable metrics to monitor rate limiting
        metrics_enabled=True,
        metrics_path="/metrics",
    )

    print("🚀 Rate Limiting Demo")
    print()
    print("Configuration:")
    print("  Rate:  5 requests/second per IP")
    print("  Burst: 10 requests (initial capacity)")
    print()
    print("Visit: http://localhost:8000/")
    print()
    print("Test from command line:")
    print("  # Should succeed (within burst)")
    print("  for i in {1..5}; do curl http://localhost:8000/api; done")
    print()
    print("  # Will hit rate limit")
    print("  for i in {1..20}; do curl http://localhost:8000/api; sleep 0.05; done")
    print()
    print("View metrics:")
    print("  curl http://localhost:8000/metrics | grep 429")
    print()

    run(app, config=config)
