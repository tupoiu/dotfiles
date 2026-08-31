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
      pkgs = import nixpkgs {
        inherit system;
        # nixpkgs refuses unfree packages by default. Allow exactly the ones we
        # want rather than opening the gate entirely.
        config.allowUnfreePredicate = pkg:
          builtins.elem (nixpkgs.lib.getName pkg) [ "claude-code" ];
      };

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

          bun
          claude-code

          # from install.sh / the Dockerfile
          uv
          poethepoet
          delta
          gh
          less
          procps

          # /etc/passwd + /etc/group. Without this most tools error on
          # "cannot look up current user". Overridden to add `node`, since the
          # stock version only knows root and nobody.
          (dockerTools.fakeNss.override {
            extraPasswdLines = [ "node:x:1000:1000:node:/home/node:/bin/fish" ];
            extraGroupLines = [ "node:x:1000:" ];
          })

          # /etc/ssl/certs — without it every HTTPS call fails.
          cacert
        ];

        # Runs under fakeroot, so chown works without real privileges. This is
        # where the Dockerfile's `mkdir -p /workspace /home/node && chown -R`
        # ends up.
        fakeRootCommands = ''
          # Pre-create every directory podman will mount into. Otherwise podman
          # creates the missing parents itself, owned by root, and the container
          # user can't write alongside them.
          mkdir -p ./home/node/.config/fish ./home/node/.claude ./workspace
          chown -R 1000:1000 ./home/node ./workspace

          # No base image means no /tmp. 1777 = world-writable with the sticky
          # bit, so anyone can create files but only delete their own.
          mkdir -p ./tmp ./var/tmp
          chmod 1777 ./tmp ./var/tmp
        '';

        config = {
          User = "node";
          Cmd = [ "/bin/fish" ];
          WorkingDir = "/workspace";
          Env = [
            "SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt"
            "HOME=/home/node"
            "SHELL=/bin/fish"
            "EDITOR=vim"
            "DEVCONTAINER=true"
            # The claude-code wrapper sets this to 1 via --set-default, which
            # would auto-update plugins out from under the pinned Superpowers
            # commit. Overridable precisely because it's a default, not a --set.
            "FORCE_AUTOUPDATE_PLUGINS=0"
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
