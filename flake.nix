{
  # A flake is an attribute set with two key fields: `inputs` (what it depends
  # on) and `outputs` (what it produces). That's the whole shape.
  description = "dotfiles dev environment";

  inputs = {
    # Pinned in flake.lock the first time you build. Nobody gets a different
    # nixpkgs until you deliberately run `nix flake update`.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    home-manager = {
      url = "github:nix-community/home-manager";
      # Make home-manager use OUR nixpkgs rather than pulling a second copy.
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  # `outputs` is a FUNCTION from the resolved inputs to what this flake exposes.
  outputs = { self, nixpkgs, home-manager }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };

      # One machine = one call to this. Everything shared lives in ./home.nix;
      # only the genuinely per-machine facts are arguments.
      mkHome = { system, username, homeDirectory }:
        home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.${system};
          modules = [ ./home.nix ];
          # Passed through to ./home.nix as function arguments.
          extraSpecialArgs = { inherit username homeDirectory; };
        };
    in
    {
      # Built with: home-manager switch --flake .#peter@ubuntu-8gb-dev
      homeConfigurations = {
        "peter-dev" = mkHome {
          system = "x86_64-linux";
          username = "peter";
          homeDirectory = "/home/peter";
        };
      };

      # Build:  nix build .#container
      # Load:   ./result | podman load
      packages.${system}.container = pkgs.dockerTools.streamLayeredImage {
        name = "claude-nix";
        tag = "latest";

        # Each store path becomes its own layer, so changing one tool
        # re-pushes one layer instead of invalidating everything below it.
        contents = with pkgs; [
          # An image built by Nix is EMPTY by default — no shell, no coreutils,
          # no /etc/passwd. Everything below is opt-in.
          bash
          coreutils
          fish
          git

          jujutsu
          ripgrep
          fzf
          jq

          # /etc/passwd + /etc/group. Without this most tools error on
          # "cannot look up current user".
          dockerTools.fakeNss

          # /etc/ssl/certs — without it every HTTPS call fails.
          cacert
        ];

        config = {
          Cmd = [ "/bin/fish" ];
          WorkingDir = "/workspace";
          Env = [
            "SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt"
            "EDITOR=vim"
          ];
        };
      };

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
