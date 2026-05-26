#!/usr/bin/env bash
input=$(cat)
prompt=$(echo "$input" | jq -r '.prompt')
op_id=$(jj op log --no-graph -T 'self.id().short()' -n1)
ts=$(date -u +%FT%TZ)
entry=$(jq -cn --arg ts "$ts" --arg op_id "$op_id" --arg prompt "$prompt" \
  '{ts: $ts, op_id: $op_id, prompt: $prompt}')
echo "$entry" >> .jj_checkpoints.jsonl
