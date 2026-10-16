Background index sync and rebuild jobs now refresh the public site cache and
persist its render outcome separately from the index result. Renderers and
HTTP routes share the configured index's cache home, and cache renders are
serialized across processes while retaining atomic page writes and pruning.
