function fish-alias --description 'Add, test and manage fish aliases in the dotfiles repo'
    set -l repo $DOTFILES_DIR
    if test -z "$repo"
        set repo (path dirname (path dirname (path dirname (realpath (status filename)))))
    end
    if not test -x $repo/fishfunc/fish_alias.py
        echo "fish-alias: no dotfiles repo at $repo. Set DOTFILES_DIR." >&2
        return 1
    end

    set -l actions (mktemp)
    FISH_ALIAS_ACTIONS=$actions $repo/fishfunc/fish_alias.py $argv
    set -l code $status

    # The CLI can't change this shell. It lists what to do instead.
    while read -l action arg
        switch $action
            case source
                source $arg
            case erase
                functions -e $arg
        end
    end <$actions
    rm -f $actions
    return $code
end
