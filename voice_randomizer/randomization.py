"""Create a reproducible mapping for one voice pool."""

import random
from collections.abc import Iterable


def _path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def shuffle_pool(paths: Iterable[str], seed: int | str) -> dict[str, str]:
    """Map each destination to a source from the same pool.

    A pool with one entry maps that entry to itself. For larger pools, every
    entry has a different source. Paths must be unique on Windows.
    """
    entries = list(paths)
    if any(not isinstance(path, str) or not path for path in entries):
        raise ValueError("Every voice path must be a non-empty string.")

    entries.sort(key=_path_key)
    keys = [_path_key(path) for path in entries]
    if len(set(keys)) != len(keys):
        raise ValueError("Voice paths must be unique, ignoring case and slashes.")

    if len(entries) < 2:
        return {path: path for path in entries}

    rng = random.Random(seed)
    sources = entries.copy()
    while True:
        rng.shuffle(sources)
        if all(destination != source for destination, source in zip(entries, sources)):
            return dict(zip(entries, sources))
