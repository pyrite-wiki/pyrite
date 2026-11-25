Building an entry with `dataclasses.replace(entry, importance=X)` resets `_importance_given` when it disagrees with `importance`, so `validation_errors` judges the replaced attribute (#1703).
