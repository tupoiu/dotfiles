function claude-local --description 'Run claude-default container with Pro login credentials'
    podman run -it --rm --userns=keep-id \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v (pwd):/workspace \
        -w /workspace claude-default /bin/zsh $argv
end
