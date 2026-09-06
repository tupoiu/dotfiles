function claude-nix --description 'Run the Nix-built claude container'
    podman run -it --rm --userns=keep-id \
        --device /dev/fuse \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v ~/.pi:/home/node/.pi \
        -v (pwd):/workspace \
        -v ~/.config/jj:/home/node/.config/jj \
        -w /workspace claude-nix:latest /bin/fish $argv
end
