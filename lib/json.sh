#!/usr/bin/env bash
# https://github.com/heroku/heroku-buildpack-nodejs/blob/main/lib/json.sh

read_json() {
  local file="$1"
  local key="$2"

  if test -f "$file"; then
    # -c = print on only one line
    # -M = strip any color
    # --raw-output = if the filter's result is a string then it will be written directly
    #                to stdout rather than being formatted as a JSON string with quotes
    jq -c -M --raw-output "$key // \"\"" "$file" || return 1
  else
    echo ""
  fi
}

json_has_key() {
  local file="$1"
  local key="$2"

  if test -f "$file"; then
    jq ". | has(\"$key\")" "$file"
  else
    echo "false"
  fi
}

has_script() {
  local file="$1"
  local key="$2"

  if test -f "$file"; then
    jq ".[\"scripts\"] | has(\"$key\")" "$file"
  else
    echo "false"
  fi
}

is_invalid_json_file() {
  local file="$1"
  if ! jq "." "$file" 1>/dev/null; then
    echo "true"
  else
    echo "false"
  fi
}
