import os
import pathlib


def cache_dir(*parts):
    base = pathlib.Path(os.environ.get("STALECRATE_CACHE")
                        or pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "stalecrate")
    d = base.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d
