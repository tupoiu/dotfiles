#!/usr/bin/bash

# Install Rust/Cargo
snap install rustup

# Python tools
cargo install --locked uv
uv tool install poethepoet

# Shell tools
sudo apt install fish

uv tool install podman-compose
uv tool install pre-commit
pre-commit install

# Claude credentials (needed for container mounts)
mkdir -p ~/.claude
touch ~/.claude.json
cp $(realpath claude-docker/claude-settings.json) ~/.claude/settings.json

# Fish functions
mkdir -p ~/.config/fish/functions
for f in fish/functions/*.fish; do
    ln -sf $(realpath $f) ~/.config/fish/functions/$(basename $f)
done

# Bash helpers
mkdir -p ~/.helpers
for f in bash-helpers/*.sh; do
    cp $(realpath $f) ~/.helpers/$(basename $f .sh)
    chmod +x ~/.helpers/$(basename $f .sh)
done
fish -c 'fish_add_path ~/.helpers'
