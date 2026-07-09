Give each HTTP/3 worker generation its own duplicated UDP listener, fully retire
the old generation before replacement, and preserve the supervisor-owned socket
across reloads so transport cleanup cannot orphan or invalidate the new worker.
