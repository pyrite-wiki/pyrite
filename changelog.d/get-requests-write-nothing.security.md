A `GET` request writes nothing, for any caller. `GET /api/daily/{date}` answers 404 for a date with no daily note instead of creating one; `POST /api/daily/{date}` creates it, as the web view's "Start today's note" already does.

**Upgrade notes:** a client that relied on the `GET` to create the day's note should `POST` to the same path.
