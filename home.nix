# `username` and `homeDirectory` arrive from flake.nix's extraSpecialArgs,
# so this file stays machine-agnostic.
{ pkgs, username, homeDirectory, ... }:

{
  home.username = username;
  home.homeDirectory = homeDirectory;

  # Not a version to bump casually — it pins backwards-compatible defaults.
  # Set it once to the Home Manager release you started on and leave it.
  home.stateVersion = "25.05";

  # Replaces the cargo binstall / apt / uv tool install blocks in install.sh.
  # These land on PATH permanently, unlike the devShell in flake.nix.
  home.packages = with pkgs; [
    jujutsu
    uv
    ripgrep
    zellij
    watchexec
    fzf
    jq
    bubblewrap

    clang
    mold
    rustup

    poethepoet
    pre-commit
    podman-compose
  ];

  # `fish_add_path ~/.helpers` and `uv tool update-shell`, declared instead.
  home.sessionPath = [
    "$HOME/.helpers"
    "$HOME/.local/bin"
  ];

  # Puts the `home-manager` CLI on PATH, so after the first bootstrap switch
  # you can drop the `nix run home-manager/master --` prefix.
  programs.home-manager.enable = true;

  programs.fish.enable = true;

  # Replaces the three `jj config set --user` calls. Generated as TOML at
  # ~/.config/jj/config.toml.
  programs.jujutsu = {
    enable = true;
    settings = {
      user.name = "Peter Tupoiu";
      user.email = "54478352+tupoiu@users.noreply.github.com";
      ui.editor = "vim";
      git.push = "upstream";
    };
  };

  # Replaces the symlink loop over fish/functions/*.fish.
  xdg.configFile."fish/functions".source = ./fish/functions;

  # Replaces the bash-helpers loop: copy to ~/.helpers/<name>, strip .sh, chmod +x.
  home.file.".helpers/claude-statusline" = {
    source = ./bash-helpers/claude-statusline.sh;
    executable = true;
  };
}
