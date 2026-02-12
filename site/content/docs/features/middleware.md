---
title: Middleware System
description: ASGI3 middleware stack for request/response transformation
weight: 2
---

# Middleware System

Pounce provides **ASGI3 middleware support** at the server level, allowing you to transform requests and responses across your entire application.

[Full middleware documentation with examples →](/docs/features/middleware)

## Configuration

```python
from pounce import ServerConfig

config = ServerConfig(
    middleware=[
        request_id_middleware,
        logging_middleware,
        auth_middleware,
    ]
)
```

## Common Use Cases

- CORS headers
- Request ID injection
- Authentication and authorization
- Request/response logging
- Custom header injection
- Rate limiting

See the [full middleware guide](/docs/features/middleware) for detailed examples.
