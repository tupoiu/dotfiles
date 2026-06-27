#!/usr/bin/env bash
# Claude Code statusLine: "eff:M Opus4.8 my-branch\n  ~/code/dotfiles"
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name // "?"' | tr -d ' ')
dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // ""')

# Effort: prefer the live value from the payload, fall back to settings.json.
effort=$(echo "$input" | jq -r '.model.effort // .effort.level // .effortLevel // empty')
if [ -z "$effort" ]; then
    settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
    [ -f "$settings" ] && effort=$(jq -r '.effortLevel // empty' "$settings" 2>/dev/null)
fi
case "$(echo "$effort" | tr '[:upper:]' '[:lower:]')" in
    low) eff=L ;;
    med|medium) eff=M ;;
    high) eff=H ;;
    "") eff="?" ;;
    *) eff=$(echo "$effort" | cut -c1 | tr '[:lower:]' '[:upper:]') ;;
esac

# Branch: git first, then jj (this repo uses jj).
branch=""
if [ -n "$dir" ] && [ -d "$dir" ]; then
    branch=$(git -C "$dir" branch --show-current 2>/dev/null)
    [ -n "$branch" ] && branch="git:$branch"
    [ -z "$branch" ] && branch=$(jj -R "$dir" log --no-graph -T '"jj:" ++ change_id.shortest().prefix()' -r @ 2>/dev/null)
fi

# Folder, with $HOME collapsed to ~.
folder="${dir/#$HOME/\~}"

printf 'eff:%s %s %s %s' "$eff" "$model" "$branch" "$folder"
