Fish config for the claude-nix container that doesn't need an image rebuild.

`claude-nix` mounts these read-only into `/home/node/.config/fish/`:

- `conf.d/` — sourced at every fish startup. Env vars, abbreviations, PATH.
- `functions/` — autoloaded, one function per file, named after the function.

Changes apply on the next container start. Anything that installs software
belongs in `flake.nix` instead.

Only fish reads these. Commands started without fish (`podman run ... pi`)
won't see them.
