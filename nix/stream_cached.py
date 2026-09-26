"""
Streams the claude-nix image to stdout, like the streamLayeredImage wrapper.

Upstream tars each layer twice. The first pass only computes the layer's
sha256 and size. Store paths are immutable, so this script caches that result.
A cache hit skips the first pass.

The second pass hashes the bytes it streams. A mismatch with the cache deletes
the entry and fails the run. A bad cache entry cannot produce a bad image.

Usage: python3 nix/stream_cached.py [CONF SCRIPT] | podman load
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = (
    pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache"))
    / "claude-nix-stream"
)


def build_inputs():
    """Returns the store paths of the image's conf.json and upstream script."""
    out = subprocess.run(
        [
            "nix", "build", "--no-link", "--json",
            f"{REPO}#container.conf", f"{REPO}#container.streamScript",
        ],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout
    conf, script = (r["outputs"]["out"] for r in json.loads(out))
    return conf, script


def load_upstream(script):
    # The script has no .py suffix, so name the loader explicitly.
    loader = importlib.machinery.SourceFileLoader("stream_layered_image", script)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def patch(up, script):
    upstream_add_layer_dir = up.add_layer_dir

    class Tee(up.ExtractChecksum):
        """Hashes the bytes and also writes them to out."""

        def __init__(self, out):
            super().__init__()
            self._out = out

        def write(self, data):
            super().write(data)
            self._out.write(data)

    def add_layer_dir(tar, paths, store_dir, mtime, uid, gid, uname, gname):
        # The script path changes when upstream's tar logic can change.
        key = json.dumps([script, paths, mtime, uid, gid, uname, gname])
        entry = CACHE / hashlib.sha256(key.encode()).hexdigest()
        try:
            checksum, size = json.loads(entry.read_text())
        except (OSError, ValueError):
            info = upstream_add_layer_dir(
                tar, paths, store_dir, mtime, uid, gid, uname, gname
            )
            CACHE.mkdir(parents=True, exist_ok=True)
            entry.write_text(json.dumps([info.checksum, info.size]))
            return info

        path = f"{checksum}/layer.tar"
        layer_tarinfo = up.tarfile.TarInfo(path)
        layer_tarinfo.size = size
        layer_tarinfo.mtime = mtime

        read_fd, write_fd = os.pipe()
        with open(read_fd, "rb") as read, open(write_fd, "wb") as write:
            tee = Tee(write)

            def producer():
                up.archive_paths_to(tee, paths, mtime, uid, gid, uname, gname)
                write.close()

            # Daemon, so a mismatch can exit while the producer is blocked.
            thread = threading.Thread(target=producer, daemon=True)
            thread.start()
            try:
                tar.addfile(layer_tarinfo, read)
                extra = read.read(1)
            except OSError:
                extra = b"short"
            if not extra:
                thread.join()
            if extra or tee.extract() != (checksum, size):
                entry.unlink(missing_ok=True)
                sys.exit(f"Stale cache entry for {paths}. Deleted it. Run again.")

        print("  (digest from cache)", file=sys.stderr)
        return up.LayerInfo(size=size, checksum=checksum, path=path, paths=paths)

    up.add_layer_dir = add_layer_dir


def main():
    # Optional args: CONF SCRIPT. They skip the nix build.
    conf, script = sys.argv[1:3] if len(sys.argv) == 3 else build_inputs()
    up = load_upstream(script)
    patch(up, script)
    sys.argv = [script, conf]
    up.main()


if __name__ == "__main__":
    main()
