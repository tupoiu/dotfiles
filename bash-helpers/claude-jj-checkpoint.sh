#!/usr/bin/env bash
input=$(cat)
prompt=$(echo "$input" | jq -r '.prompt')
# op_id     -> jj op restore <op_id>        : rolls back entire repo state (commits, branches, op log)
# commit_id -> jj restore --from <commit_id>: restores working-copy file contents to this exact snapshot
# change_id    stable across rebases/amends; locates the change even after its commit_id changes
op_id=$(jj op log --no-graph -T 'self.id().short()' -n1)
read -r commit_id change_id < <(jj log --no-graph -T 'commit_id.short() ++ " " ++ change_id.short() ++ "\n"' -r @)
ts=$(date -u +%FT%TZ)
entry=$(jq -cn --arg ts "$ts" --arg op_id "$op_id" --arg commit_id "$commit_id" --arg change_id "$change_id" --arg prompt "$prompt" \
  '{ts: $ts, op_id: $op_id, commit_id: $commit_id, change_id: $change_id, prompt: $prompt}')
echo "$entry" >> .jj_checkpoints.jsonl
