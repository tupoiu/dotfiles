{
  # A flake is an attribute set with two key fields: `inputs` (what it depends
  # on) and `outputs` (what it produces). That's the whole shape.
  description = "dotfiles dev environment";

  inputs = {
    # Pinned in flake.lock the first time you build. Nobody gets a different
    # nixpkgs until you deliberately run `nix flake update`.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  # `outputs` is a FUNCTION from the resolved inputs to what this flake exposes.
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        # Everything here goes on PATH inside the shell, and nowhere else.
        packages = with pkgs; [
          # from install.sh
          jujutsu
          uv
          ripgrep
          zellij
          watchexec
          fzf
          fish
          jq
          bubblewrap

          # build toolchain
          clang
          mold

          # python tooling (installed via `uv tool install` today)
          poethepoet
          pre-commit
        ];

        shellHook = ''
          echo "dotfiles devshell — jj $(jj --version)"
        '';
      };
    };
}
