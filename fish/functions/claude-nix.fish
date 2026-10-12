function claude-nix --description 'Run the Nix-built claude container'
    # This file is symlinked into ~/.config/fish/functions; resolve to the repo.
    set -l repo (path resolve (functions --details claude-nix) | path dirname | path dirname | path dirname)
    podman run -it --rm --userns=keep-id \
        --device /dev/fuse \
        -e UV_PROJECT_ENVIRONMENT=/home/node/venv \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v ~/.pi:/home/node/.pi \
        -v (pwd):/workspace:rshared \
        -v uv-cache:/home/node/.cache/uv \
        -v ~/.config/jj:/home/node/.config/jj \
        -v $repo/container-fish/conf.d:/home/node/.config/fish/conf.d:ro \
        -v $repo/container-fish/functions:/home/node/.config/fish/functions:ro \
        -w /workspace claude-nix:latest /bin/fish $argv
end
