function fe-local --description 'Run playwright-default container with Pro login credentials'
    podman run -it --rm --userns=keep-id \
        -e UV_PROJECT_ENVIRONMENT=/home/node/venv \
        -v ~/.claude:/home/node/.claude \
        -v ~/.claude.json:/home/node/.claude.json \
        -v (pwd):/workspace \
        -v uv-cache:/home/node/.cache/uv \
        -v ~/.config/jj:/home/node/.config/jj \
        -w /workspace playwright-default /bin/fish $argv
end
