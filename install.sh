# Install Rust/Cargo
snap install rustup

# Python tools
cargo install --locked uv
uv tool install poethepoet

# Shell tools
sudo apt install fish

uv tool install podman-compose

# Fish functions
mkdir -p ~/.config/fish/functions
for f in fish/functions/*.fish
    ln -sf (realpath $f) ~/.config/fish/functions/(basename $f)
end