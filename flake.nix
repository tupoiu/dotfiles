{
  description = "dotfiles dev environment";

  inputs = {
    # flake.lock pins this. It changes only on `nix flake update`.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    home-manager = {
      url = "github:nix-community/home-manager";
      # Use the nixpkgs above. Do not pull a second copy.
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        # nixpkgs refuses unfree packages. Allow these by name.
        config.allowUnfreePredicate = pkg:
          builtins.elem (nixpkgs.lib.getName pkg) [ "claude-code" ];
      };

      # One call per machine. Shared settings live in ./home.nix. Arguments are
      # the per-machine facts.
      mkHome = { system, username, homeDirectory }:
        home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.${system};
          modules = [ ./home.nix ];
          # ./home.nix takes these as function arguments.
          extraSpecialArgs = { inherit username homeDirectory; };
        };
    in
    {
      # Apply: home-manager switch --flake .#peter-dev
      homeConfigurations = {
        "peter-dev" = mkHome {
          system = "x86_64-linux";
          username = "peter";
          homeDirectory = "/home/peter";
        };
      };

      # Build: nix build .#container
      # Load:  ./result | podman load
      packages.${system}.container = pkgs.dockerTools.streamLayeredImage {
        name = "claude-nix";
        tag = "latest";

        # One layer per store path. A changed tool invalidates its own layer
        # only.
        contents = with pkgs; [
          # A Nix image starts empty. It has no shell, no coreutils and no
          # /etc/passwd. Add each one here.
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
          which

          # coreutils excludes sed, grep, awk, find and tar. Add them here.
          gnused
          gnugrep
          gawk
          findutils
          diffutils
          gnutar
          gzip
          curl
          # git and jj run the ssh binary. The remote uses ssh://.
          openssh

          # fd does not replace find, so findutils stays above.
          fd

          python3
          unzip
          patch

          # /usr/bin/env, for `#!/usr/bin/env python3` shebangs.
          dockerTools.usrBinEnv

          # Locale data. Without it, LANG is unset and Unicode sorting fails
          # silently.
          glibcLocales

          # Editor
          neovim

          # /etc/passwd and /etc/group. Without them, most tools fail to look up
          # the current user. The stock version knows root and nobody only, so
          # add node.
          (dockerTools.fakeNss.override {
            extraPasswdLines = [ "node:x:1000:1000:node:/home/node:/bin/fish" ];
            extraGroupLines = [ "node:x:1000:" ];
          })

          # /etc/ssl/certs. Without it, every HTTPS call fails.
          cacert

          # Nix binaries hold the loader's store path, so they do not need these.
          # Binaries fetched at run time hold the path /lib64/ld-linux-*.so.2
          # instead. Examples: uv's CPython, npm native modules, downloaded CLIs.
          # Without these they fail with "no glibc loader". See fakeRootCommands.
          glibc
          stdenv.cc.cc.lib
          zlib
        ];

        # Runs under fakeroot, so chown needs no privileges. This replaces the
        # Dockerfile's `mkdir -p /workspace /home/node && chown -R`.
        fakeRootCommands = ''
          # Create every mount point first. Otherwise podman creates the missing
          # parents as root and the container user cannot write beside them.
          mkdir -p ./home/node/.config/fish ./home/node/.config/jj \
            ./home/node/.claude ./home/node/.pi \
            ./home/node/.cache/uv ./home/node/.cache/fish \
            ./home/node/venv ./workspace
          chown -R 1000:1000 ./home/node ./workspace

          # No base image means no /tmp. Mode 1777 lets any user create files
          # and lets each user delete only their own.
          mkdir -p ./tmp ./var/tmp
          chmod 1777 ./tmp ./var/tmp

          # glibc puts the loader at /lib/ld-linux-x86-64.so.2. Foreign binaries
          # look in /lib64. This symlink makes them run.
          mkdir -p ./lib64
          ln -sf ../lib/ld-linux-x86-64.so.2 ./lib64/ld-linux-x86-64.so.2
        '';

        config = {
          User = "node";
          Cmd = [ "/bin/fish" ];
          WorkingDir = "/workspace";
          Env = [
            "SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt"
            "HOME=/home/node"
            "SHELL=/bin/fish"
            "EDITOR=nvim"
            "DEVCONTAINER=true"
            "LANG=C.UTF-8"
            "LC_ALL=C.UTF-8"
            # The claude-code wrapper sets this to 1 with --set-default. That
            # auto-updates plugins past the pinned Superpowers commit. A default
            # is overridable here; a --set value is not.
            "FORCE_AUTOUPDATE_PLUGINS=0"
            # Foreign binaries also load libstdc++, libz and libm by soname.
            # Nix binaries carry an RPATH and ignore this variable.
            "LD_LIBRARY_PATH=/lib"
          ];
        };
      };

      devShells.${system}.default = pkgs.mkShell {
        # These go on PATH inside the shell only.
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
