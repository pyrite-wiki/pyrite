The server now limits the size of a request body, an import upload included,
to `settings.max_request_body_bytes` (default 32 MiB; environment variable
`PYRITE_MAX_REQUEST_BODY_BYTES`). A larger request is answered `413` with
`code: PAYLOAD_TOO_LARGE` and nothing is written; raise the setting or split
the file to import more. An import file that cannot be read in its format now
answers `400 PARSE_ERROR` with a message naming the format; the parser's
detail goes to the server log.
