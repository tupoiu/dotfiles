#!/usr/bin/bash

# Symlink the repo's fish functions into ~/.config/fish/functions
# Safe to run standalone or from install.sh

repo_dir=$(dirname "$(realpath "$0")")

mkdir -p ~/.config/fish/functions
for f in "$repo_dir"/fish/functions/*.fish; do
    ln -sf "$(realpath "$f")" ~/.config/fish/functions/"$(basename "$f")"
done
