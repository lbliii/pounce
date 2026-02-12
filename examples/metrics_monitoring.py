"""
Prometheus metrics monitoring example.

Demonstrates pounce's built-in Prometheus metrics endpoint with a
dashboard showing real-time metrics.

Exposed metrics:
- http_requests_total: Total requests by method and status
- http_request_duration_seconds: Request latency histogram
- http_connections_active: Current active connections
- http_requests_in_flight: Currently processing requests
- http_bytes_sent_total: Total bytes sent in responses

Run:
    python examples/metrics_monitoring.py

Then:
    1. Visit http://localhost:8000/ for dashboard
    2. Visit http://localhost:8000/metrics for raw Prometheus metrics
    3. Generate load: ab -n 1000 -c 10 http://localhost:8000/api/fast

Integrate with Prometheus:
    Add to prometheus.yml:

    scrape_configs:
      - job_name: 'pounce-demo'
        static_configs:
          - targets: ['localhost:8000']
        metrics_path: '/metrics'
        scrape_interval: 5s

"""

import asyncio
import random
import time

from pounce import run, ServerConfig


async def app(scope, receive, send):
    """Demo API with various endpoints for metrics testing."""
    if scope["type"] != "http":
        return

    path = scope["path"]

    # Dashboard
    if path == "/" or path == "/dashboard":
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
                <title>Metrics Monitoring Dashboard</title>
                <style>
                    body { font-family: system-ui; max-width: 1200px; margin: 20px auto; padding: 20px; background: #f5f5f5; }
                    .card { background: white; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                    h1 { color: #333; }
                    h2 { color: #666; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }
                    .metric { font-size: 48px; font-weight: bold; color: #4CAF50; }
                    .label { color: #666; font-size: 14px; text-transform: uppercase; }
                    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
                    button { padding: 12px 24px; font-size: 16px; cursor: pointer; background: #4CAF50; color: white; border: none; border-radius: 4px; }
                    button:hover { background: #45a049; }
                    code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: monospace; }
                    pre { background: #2d2d2d; color: #f8f8f8; padding: 15px; border-radius: 5px; overflow-x: auto; }
                    .endpoint { margin: 10px 0; padding: 10px; background: #f9f9f9; border-radius: 4px; }
                </style>
            </head>
            <body>
                <h1>📊 Metrics Monitoring Dashboard</h1>

                <div class="card">
                    <h2>Live Metrics</h2>
                    <div class="grid">
                        <div>
                            <div class="label">Total Requests</div>
                            <div class="metric" id="total-requests">0</div>
                        </div>
                        <div>
                            <div class="label">Active Connections</div>
                            <div class="metric" id="active-connections">0</div>
                        </div>
                        <div>
                            <div class="label">P95 Latency (ms)</div>
                            <div class="metric" id="p95-latency">0</div>
                        </div>
                        <div>
                            <div class="label">Error Rate (%)</div>
                            <div class="metric" id="error-rate">0</div>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <h2>Generate Load</h2>
                    <button onclick="generateLoad(10, 'fast')">Send 10 Fast Requests</button>
                    <button onclick="generateLoad(10, 'slow')">Send 10 Slow Requests</button>
                    <button onclick="generateLoad(5, 'error')">Trigger 5 Errors</button>
                </div>

                <div class="card">
                    <h2>Test Endpoints</h2>
                    <div class="endpoint">
                        <strong>GET /api/fast</strong> - Fast response (~10ms)
                    </div>
                    <div class="endpoint">
                        <strong>GET /api/slow</strong> - Slow response (~500ms)
                    </div>
                    <div class="endpoint">
                        <strong>GET /api/error</strong> - Returns 500 error
                    </div>
                    <div class="endpoint">
                        <strong>GET /metrics</strong> - Prometheus metrics (raw)
                    </div>
                </div>

                <div class="card">
                    <h2>Prometheus Metrics</h2>
                    <p>View raw metrics: <a href="/metrics" target="_blank">http://localhost:8000/metrics</a></p>
                    <p>Or query with curl:</p>
                    <pre>curl http://localhost:8000/metrics</pre>
                    <p>Integrate with Prometheus by adding to <code>prometheus.yml</code>:</p>
                    <pre>scrape_configs:
  - job_name: 'pounce-demo'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 5s</pre>
                </div>

                <script>
                    async function updateMetrics() {
                        try {
                            const response = await fetch('/metrics');
                            const text = await response.text();

                            // Parse Prometheus metrics (simple parsing)
                            const totalRequests = (text.match(/http_requests_total{.*} (\\d+)/g) || [])
                                .reduce((sum, line) => sum + parseInt(line.match(/\\d+$/)[0]), 0);

                            const activeConnections = (text.match(/http_connections_active (\\d+)/) || [0, 0])[1];

                            // Extract P95 from histogram
                            const p95Match = text.match(/http_request_duration_seconds{quantile="0.95"} ([\\d.]+)/);
                            const p95Latency = p95Match ? (parseFloat(p95Match[1]) * 1000).toFixed(2) : '0';

                            // Calculate error rate
                            const errorMatches = text.match(/http_requests_total{method="[^"]+",status="5\\d+"} (\\d+)/g) || [];
                            const errorCount = errorMatches.reduce((sum, line) => sum + parseInt(line.match(/\\d+$/)[0]), 0);
                            const errorRate = totalRequests > 0 ? ((errorCount / totalRequests) * 100).toFixed(2) : '0';

                            document.getElementById('total-requests').textContent = totalRequests;
                            document.getElementById('active-connections').textContent = activeConnections;
                            document.getElementById('p95-latency').textContent = p95Latency;
                            document.getElementById('error-rate').textContent = errorRate;
                        } catch (e) {
                            console.error('Failed to fetch metrics:', e);
                        }
                    }

                    async function generateLoad(count, type) {
                        const endpoint = `/api/${type}`;
                        for (let i = 0; i < count; i++) {
                            fetch(endpoint).catch(e => console.error(e));
                            await new Promise(r => setTimeout(r, 100));
                        }
                    }

                    // Update metrics every 2 seconds
                    updateMetrics();
                    setInterval(updateMetrics, 2000);
                </script>
            </body>
            </html>
            """,
        })
        return

    # Fast API endpoint (~10ms)
    if path == "/api/fast":
        await asyncio.sleep(0.01)  # 10ms
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"status": "ok", "latency": "10ms"}',
        })
        return

    # Slow API endpoint (~500ms)
    if path == "/api/slow":
        await asyncio.sleep(0.5)  # 500ms
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"status": "ok", "latency": "500ms"}',
        })
        return

    # Error endpoint (for testing error rate)
    if path == "/api/error":
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"error": "Simulated error for metrics demo"}',
        })
        return

    # 404
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
        workers=4,

        # Enable Prometheus metrics
        metrics_enabled=True,
        metrics_path="/metrics",

        # Enable lifecycle logging for more detailed metrics
        lifecycle_logging=True,
    )

    print("📊 Metrics Monitoring Demo")
    print()
    print("Dashboard:     http://localhost:8000/")
    print("Prometheus:    http://localhost:8000/metrics")
    print()
    print("Generate load with Apache Bench:")
    print("  ab -n 1000 -c 10 http://localhost:8000/api/fast")
    print()
    print("Query metrics with curl:")
    print("  curl http://localhost:8000/metrics | grep http_requests_total")
    print()

    run(app, config=config)
