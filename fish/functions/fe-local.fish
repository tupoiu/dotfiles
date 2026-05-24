function fe-local --description 'Run playwright-default container with Pro login credentials'
    podman run -it --rm --userns=keep-id \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v (pwd):/workspace \
        -v ~/.config/jj:/home/node/.config/jj \
        -w /workspace playwright-default /bin/fish $argv
end
