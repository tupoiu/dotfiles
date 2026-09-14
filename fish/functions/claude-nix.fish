function claude-nix --description 'Run the Nix-built claude container'
    podman run -it --rm --userns=keep-id \
        --device /dev/fuse \
        -e UV_PROJECT_ENVIRONMENT=/home/node/venv \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v ~/.pi:/home/node/.pi \
        -v (pwd):/workspace:rshared \
        -v uv-cache:/home/node/.cache/uv \
        -v ~/.config/jj:/home/node/.config/jj \
        -w /workspace claude-nix:latest /bin/fish $argv
end
