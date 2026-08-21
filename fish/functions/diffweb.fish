function diffweb --description 'Start the local git worktree diff viewer and print its URL'
    # This file is symlinked into ~/.config/fish/functions, so resolve back to the repo.
    set -l repo (dirname (dirname (dirname (realpath (status filename)))))
    set -l here (pwd)
    cd $repo

    # The repo is not an installed package, so diffweb is imported from the tree.
    set -lx PYTHONPATH $repo
    set -l url (uv run python -c \
        'from diffweb.config import load_config as l; c = l(); print(f"http://{c.server.host}:{c.server.port}")')

    if curl -sf --max-time 2 $url/healthz >/dev/null
        cd $here
        echo "diffweb already running at $url"
        return 0
    end

    uv run python -m diffweb >/tmp/diffweb.log 2>&1 &
    disown
    cd $here

    for i in (seq 30)
        curl -sf --max-time 1 $url/healthz >/dev/null; and break
        sleep 0.5
    end
    echo "diffweb at $url (log: /tmp/diffweb.log)"
end
