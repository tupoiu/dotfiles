function claude-local --description 'Run claude-default container with Pro login credentials'
    podman run -it --rm --userns=keep-id \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v (pwd):/workspace \
        -v ~/.config/jj:/home/node/.config/jj \
        -w /workspace claude-default /bin/fish $argv
end
