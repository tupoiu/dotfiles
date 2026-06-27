#!/usr/bin/bash

# Install Rust/Cargo
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

sudo apt install -y build-essential
sudo apt install -y mold clang

cargo install cargo-binstall

cargo binstall -y jj-cli
cargo binstall -y uv

# Python tools
uv tool install poethepoet

# Shell tools
sudo apt install -y fish
uv tool update-shell
source ~/.profile
fish -c 'fish_add_path ~/.local/bin'

# Podman
sudo apt install -y podman
uv tool install podman-compose

# Pre-commit
uv tool install pre-commit
source ~/.profile
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

# Make fish the default shell
sudo chsh -s "$(which fish)" $USER
