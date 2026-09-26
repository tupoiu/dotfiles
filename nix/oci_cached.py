"""
Builds the claude-nix image as an OCI layout and pulls it into podman.

streamLayeredImage streams the whole image on every run, and podman reads all
of it. Here, each layer is a cached blob named by its sha256. A layer is tarred
only when its store paths change. podman skips blobs it already has without
reading them.

The layer bytes come from upstream's archive_paths_to. The config is built the
same way as upstream's. So the image ID matches upstream's image.

Usage: python3 nix/oci_cached.py [CONF SCRIPT]
PODMAN overrides the podman command.
"""

import concurrent.futures
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import multiprocessing
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = (
    pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache"))
    / "claude-nix-oci"
)
LAYOUT = CACHE / "oci"
BLOBS = LAYOUT / "blobs" / "sha256"
INDEX = CACHE / "index"
PODMAN = os.environ.get("PODMAN", "podman")

# Upstream's stream script, loaded as a module. Forked workers inherit it.
up = None


def build_inputs():
    """Returns the store paths of the image's conf.json and upstream script."""
    out = subprocess.run(
        [
            "nix", "build", "--no-link", "--json",
            f"{REPO}#container.conf", f"{REPO}#container.streamScript",
        ],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout
    paths = [r["outputs"]["out"] for r in json.loads(out)]
    # Match by name. The JSON order is not documented.
    conf = next(p for p in paths if p.endswith("-conf.json"))
    script = next(p for p in paths if p.endswith("-stream"))
    return conf, script


def load_upstream(script):
    # The script has no .py suffix, so name the loader explicitly.
    loader = importlib.machinery.SourceFileLoader("stream_layered_image", script)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HashingFile:
    """Writes to a file. Also computes the sha256 and size of the bytes."""

    def __init__(self, f):
        self._f = f
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data):
        self.digest.update(data)
        self.size += len(data)
        self._f.write(data)


def tar_layer(paths, mtime, uid, gid, uname, gname):
    """Tars one layer into BLOBS. Returns its sha256 and size."""
    with tempfile.NamedTemporaryFile(dir=BLOBS, prefix=".tmp-", delete=False) as f:
        out = HashingFile(f)
        up.archive_paths_to(out, paths, mtime, uid, gid, uname, gname)
    checksum = out.digest.hexdigest()
    os.replace(f.name, BLOBS / checksum)
    return checksum, out.size


def put_blob(data):
    """Writes bytes to BLOBS. Returns an OCI descriptor without mediaType."""
    checksum = hashlib.sha256(data).hexdigest()
    path = BLOBS / checksum
    if not path.exists():
        tmp = BLOBS / f".tmp-{checksum}"
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return {"digest": f"sha256:{checksum}", "size": len(data)}


def build_layout(conf_path, script):
    """Writes the OCI layout. Returns the image's repo tag."""
    global up
    up = load_upstream(script)
    conf = json.loads(pathlib.Path(conf_path).read_text())
    assert conf["from_image"] is None, "fromImage is not supported."

    created = up.parse_time(conf["created"])
    mtime = int(up.parse_time(conf["mtime"]).timestamp())
    owner = (int(conf["uid"]), int(conf["gid"]), conf["uname"], conf["gname"])

    # A key names everything that changes the tar bytes.
    def key(paths):
        k = json.dumps([sys.version, script, paths, mtime, *owner])
        return INDEX / hashlib.sha256(k.encode()).hexdigest()

    layers = {}  # position -> (checksum, size, paths)
    misses = []
    for i, paths in enumerate(conf["store_layers"]):
        try:
            checksum, size = json.loads(key(paths).read_text())
            if (BLOBS / checksum).stat().st_size != size:
                raise ValueError
            layers[i] = (checksum, size, paths)
        except (OSError, ValueError):
            misses.append(i)
    print(
        f"{len(layers)} layers cached. Tarring {len(misses)}.", file=sys.stderr
    )

    if misses:
        # Fork, so the workers inherit 'up'. forkserver cannot pickle it.
        ctx = multiprocessing.get_context("fork")
        with concurrent.futures.ProcessPoolExecutor(mp_context=ctx) as pool:
            jobs = {
                i: pool.submit(tar_layer, conf["store_layers"][i], mtime, *owner)
                for i in misses
            }
            for i, job in jobs.items():
                checksum, size = job.result()
                paths = conf["store_layers"][i]
                key(paths).write_text(json.dumps([checksum, size]))
                layers[i] = (checksum, size, paths)
                print(f"  tarred layer {i + 1}: {paths}", file=sys.stderr)

    # The customisation layer is prebuilt. nix computed its sha256.
    custom = pathlib.Path(conf["customisation_layer"])
    checksum = (custom / "checksum").read_text().strip()
    if not (BLOBS / checksum).exists():
        tmp = BLOBS / f".tmp-{checksum}"
        shutil.copyfile(custom / "layer.tar", tmp)
        os.replace(tmp, BLOBS / checksum)
    ordered = [layers[i] for i in range(len(layers))]
    ordered.append(
        (checksum, (BLOBS / checksum).stat().st_size, [str(custom)])
    )

    # Same fields, order and formatting as upstream. So the image ID matches.
    image_json = {
        "created": created.isoformat(),
        "architecture": conf["architecture"],
        "os": "linux",
        "config": conf["config"],
        "rootfs": {
            "diff_ids": [f"sha256:{c}" for c, _, _ in ordered],
            "type": "layers",
        },
        "history": [
            {"created": created.isoformat(), "comment": f"store paths: {p}"}
            for _, _, p in ordered
        ],
    }
    config = put_blob(json.dumps(image_json, indent=4).encode("utf-8"))
    manifest = put_blob(json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **config},
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar",
                "digest": f"sha256:{c}",
                "size": s,
            }
            for c, s, _ in ordered
        ],
    }).encode("utf-8"))
    index = {
        "schemaVersion": 2,
        "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            **manifest,
            "annotations": {"org.opencontainers.image.ref.name": "latest"},
        }],
    }
    tmp = LAYOUT / ".tmp-index.json"
    tmp.write_text(json.dumps(index))
    os.replace(tmp, LAYOUT / "index.json")
    (LAYOUT / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')

    # Keep only this image's blobs. The cache holds one image.
    keep = {c for c, _, _ in ordered}
    keep |= {config["digest"][7:], manifest["digest"][7:]}
    for blob in BLOBS.iterdir():
        if blob.name not in keep:
            blob.unlink()
    return conf["repo_tag"]


def pull(repo_tag):
    """Pulls the layout into podman and tags it repo_tag."""
    # podman 4 names the image after the layout's path. A path part that
    # starts with "." (as in ~/.cache) is not a valid name. A symlink avoids
    # this.
    run_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    link = pathlib.Path(run_dir) / "claude-nix-oci"
    link.unlink(missing_ok=True)
    link.symlink_to(LAYOUT)

    image_id = subprocess.run(
        [PODMAN, "pull", "-q", f"oci:{link}:latest"],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout.strip()
    subprocess.run([PODMAN, "tag", image_id, repo_tag], check=True)

    # Remove the name that the pull gave. podman 4 uses the path. podman 5
    # uses the ref "latest", as docker.io/library/latest:latest.
    pulled = {f"localhost{link}:latest", "docker.io/library/latest:latest"}
    names = json.loads(subprocess.run(
        [PODMAN, "image", "inspect", "--format", "{{json .RepoTags}}", image_id],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout)
    for name in pulled.intersection(names or []):
        subprocess.run([PODMAN, "untag", image_id, name], check=True)
    print(f"Loaded {repo_tag} ({image_id[:12]}).", file=sys.stderr)


def main():
    # Optional args: CONF SCRIPT. They skip the nix build.
    conf, script = sys.argv[1:3] if len(sys.argv) == 3 else build_inputs()
    BLOBS.mkdir(parents=True, exist_ok=True)
    INDEX.mkdir(parents=True, exist_ok=True)
    # One run at a time. A second run could delete the first run's blobs.
    with open(CACHE / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        repo_tag = build_layout(conf, script)
        pull(repo_tag)


if __name__ == "__main__":
    main()
