- **The REST API's OpenAPI document reports the installed version (#1005).**
  `/openapi.json`, `/docs` and `/redoc` showed a hard-coded `0.12.0`; they now
  show `pyrite.__version__`, the same version `pyrite --version` prints.
