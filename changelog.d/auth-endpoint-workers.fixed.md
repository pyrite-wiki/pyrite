Run remaining synchronous authentication endpoint database operations in FastAPI worker threads so SQLite write locks do not block unrelated requests.
