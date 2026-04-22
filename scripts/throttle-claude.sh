#!/bin/bash
# throttle-claude.sh — resource limiter for Claude Code CLI sessions.
#
# WHAT THIS DOES:
#   - Kills Claude Code CLI sessions with cputime > MAX_CPU_SECONDS (runaway guard).
#   - Caps concurrent CLI sessions at MAX_CONCURRENT; kills the oldest if exceeded.
#   - Applies background QoS (taskpolicy -c background) + renice + cpulimit to survivors.
#
# WHAT THIS DOES NOT DO (BY DESIGN):
#   - Never touches /Applications/Claude.app (main, Claude Helper, Claude Helper (Renderer),
#     GPU/audio/network utility helpers).
#   - Never touches the bun worker daemon.
#   - Never SIGTERMs the visible Desktop window renderer.
#
# RATIONALE:
#   Applying cpulimit / taskpolicy -c background / renice to Chromium/Electron GUI
#   processes collides with Chromium's HangWatcher + IPC watchdog. cpulimit's
#   SIGSTOP/SIGCONT duty-cycle triggers "renderer unresponsive" kills; Background
#   QoS on Apple Silicon pins the UI main-thread to E-cores with no promotion path;
#   external SIGTERM of a renderer drops the visible window. All four mechanisms
#   are safe on CLI batch processes but unsafe on the Desktop app GUI — so this
#   script targets only the CLI binary.
#
#   Research + verdict: see the "Fix Claude Desktop crashes" change that replaced
#   the prior GUI-inclusive throttle. Commit history on this file has the details.
#
# Tuning (env vars):
#   THROTTLE_INTERVAL               default 30     (seconds between ticks)
#   THROTTLE_MAX_CPU_SECONDS        default 1200   (20 CPU-min → kill as runaway)
#   THROTTLE_MAX_CONCURRENT         default 4      (max simultaneous CLIs — 8 GB ceiling)
#   THROTTLE_TOTAL_CPU_BUDGET_PCT   default 240    (30% × 8 cores — heat cap)
#   THROTTLE_PROTECT_PIDS           comma-sep PIDs never killed (still cpulimited)

set -u

# Log lives OUTSIDE ~/Documents because launchd-spawned /bin/bash lacks TCC
# Full Disk Access and hits "Operation not permitted" writing into ~/Documents.
LOG="${THROTTLE_LOG:-/Users/motomoto/Library/Logs/throttle_claude.log}"
mkdir -p "$(dirname "$LOG")"

INTERVAL="${THROTTLE_INTERVAL:-30}"
MAX_CPU_SECONDS="${THROTTLE_MAX_CPU_SECONDS:-1200}"
MAX_CONCURRENT="${THROTTLE_MAX_CONCURRENT:-4}"
TOTAL_CPU_BUDGET_PCT="${THROTTLE_TOTAL_CPU_BUDGET_PCT:-240}"
PROTECT_PIDS="${THROTTLE_PROTECT_PIDS:-}"
CPULIMIT_BIN="/opt/homebrew/bin/cpulimit"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) $*" >>"$LOG"; }

is_protected() {
  local pid="$1"
  [ -z "$PROTECT_PIDS" ] && return 1
  case ",$PROTECT_PIDS," in
    *,"$pid",*) return 0 ;;
  esac
  return 1
}

# Parse ps `time` field ([D-]HH:MM:SS or MM:SS[.ff]) to seconds.
# Force base-10 on every numeric part so leading zeros don't trigger octal.
time_to_secs() {
  local t="${1%%.*}"
  local d=0 h=0 m=0 s=0 a b c
  if [[ "$t" == *-* ]]; then
    d="${t%%-*}"; t="${t#*-}"
  fi
  IFS=: read -r a b c <<<"$t"
  if [ -z "${c:-}" ]; then m="$a"; s="$b"; else h="$a"; m="$b"; s="$c"; fi
  echo $(( 10#${d:-0}*86400 + 10#${h:-0}*3600 + 10#${m:-0}*60 + 10#${s:-0} ))
}

# CLI targets — Claude Code CLI binary (scheduled agents, IDE-driven sessions).
# Format per line: pid etime time
snapshot_cli() {
  ps -Ao pid,etime,time,command | awk '
    {
      cmd=""
      for (i=4; i<=NF; i++) cmd = cmd " " $i
      if (cmd ~ /claude-code\/.*\/claude\.app\/Contents\/MacOS\/claude /) {
        print $1, $2, $3
      }
    }'
}

# Watcher management without associative arrays (macOS ships bash 3.2).
# Each tick: kill all cpulimits, then spawn one per target. cpulimit's -p
# flag implies -z, so watchers auto-exit if their target dies, but we don't
# rely on that — we fully reset every tick so -l reflects current N.
kill_all_watchers() {
  pkill -x cpulimit 2>/dev/null || true
}

spawn_watcher() {
  local target="$1" limit="$2"
  "$CPULIMIT_BIN" -p "$target" -l "$limit" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
}

count_watchers() {
  pgrep -x cpulimit 2>/dev/null | wc -l | tr -d ' '
}

tick() {
  local PIDS=() ETIMES=() CPUSECS=()
  local pid et tf
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    pid=$(awk '{print $1}' <<<"$row")
    et=$(awk '{print $2}' <<<"$row")
    tf=$(awk '{print $3}' <<<"$row")
    PIDS+=("$pid")
    ETIMES+=("$(time_to_secs "$et")")
    CPUSECS+=("$(time_to_secs "$tf")")
  done < <(snapshot_cli)

  if [ ${#PIDS[@]} -eq 0 ]; then
    kill_all_watchers
    return
  fi

  local KILLED=0 i cs et

  # Layer 1: runaway kill (cputime > MAX_CPU_SECONDS)
  local SP=() SE=() SC=()
  for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}" ; et="${ETIMES[$i]}" ; cs="${CPUSECS[$i]}"
    if is_protected "$pid"; then
      SP+=("$pid"); SE+=("$et"); SC+=("$cs")
      continue
    fi
    if [ "$cs" -gt "$MAX_CPU_SECONDS" ]; then
      log "kill(cli-runaway) pid=$pid etime=${et}s cputime=${cs}s (>${MAX_CPU_SECONDS}s)"
      kill -TERM "$pid" 2>/dev/null || true
      ( sleep 10; kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null ) &
      disown 2>/dev/null || true
      KILLED=$((KILLED+1))
    else
      SP+=("$pid"); SE+=("$et"); SC+=("$cs")
    fi
  done
  PIDS=("${SP[@]}"); ETIMES=("${SE[@]}"); CPUSECS=("${SC[@]}")

  # Layer 2: concurrency cap — kill oldest if over MAX_CONCURRENT
  if [ "${#PIDS[@]}" -gt "$MAX_CONCURRENT" ]; then
    local need=$(( ${#PIDS[@]} - MAX_CONCURRENT ))
    local KL
    KL=$(
      for i in "${!PIDS[@]}"; do
        is_protected "${PIDS[$i]}" && continue
        echo "${ETIMES[$i]} ${PIDS[$i]}"
      done | sort -rn | head -n "$need" | awk '{print $2}'
    )
    local KLA=()
    for pid in $KL; do
      log "kill(cli-over-concurrency) pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
      ( sleep 10; kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null ) &
      disown 2>/dev/null || true
      KILLED=$((KILLED+1))
      KLA+=("$pid")
    done
    # Filter out killed pids. Guarded expansion (${arr[@]+"${arr[@]}"}) so an
    # empty KLA doesn't trip `set -u` on bash 3.2 (macOS default).
    SP=(); SE=(); SC=()
    for i in "${!PIDS[@]}"; do
      local keep=1 k
      for k in ${KLA[@]+"${KLA[@]}"}; do [ "${PIDS[$i]}" = "$k" ] && keep=0 && break; done
      if [ "$keep" = "1" ]; then
        SP+=("${PIDS[$i]}"); SE+=("${ETIMES[$i]}"); SC+=("${CPUSECS[$i]}")
      fi
    done
    PIDS=(${SP[@]+"${SP[@]}"}); ETIMES=(${SE[@]+"${SE[@]}"}); CPUSECS=(${SC[@]+"${SC[@]}"})
  fi

  # Layer 3: QoS + nice for CLI survivors only
  for pid in "${PIDS[@]}"; do
    taskpolicy -c background -p "$pid" >/dev/null 2>&1 || true
    renice +15 -p "$pid" >/dev/null 2>&1 || true
  done

  # Layer 4: cpulimit budget across CLI survivors only
  local N_ALIVE=${#PIDS[@]}
  local PER_PROC=0
  if [ "$N_ALIVE" -gt 0 ] && [ -x "$CPULIMIT_BIN" ]; then
    PER_PROC=$(( TOTAL_CPU_BUDGET_PCT / N_ALIVE ))
    [ "$PER_PROC" -lt 5 ] && PER_PROC=5

    kill_all_watchers
    sleep 0.3
    for pid in "${PIDS[@]}"; do
      spawn_watcher "$pid" "$PER_PROC"
    done
  fi

  if [ "$KILLED" -gt 0 ] || [ "$N_ALIVE" -gt 0 ]; then
    log "tick: killed=$KILLED cli=${#PIDS[@]} budget_total=${TOTAL_CPU_BUDGET_PCT}% per_proc=${PER_PROC}% watchers=$(count_watchers)"
  fi
}

# Handle SIGTERM cleanly (launchctl bootout): kill watchers before exiting.
cleanup() {
  kill_all_watchers
  log "daemon exit"
  exit 0
}
trap cleanup TERM INT

log "daemon start (interval=${INTERVAL}s budget=${TOTAL_CPU_BUDGET_PCT}% max=${MAX_CONCURRENT} kill_cputime=${MAX_CPU_SECONDS}s) — CLI-only, Desktop app untouched"

while true; do
  tick
  sleep "$INTERVAL"
done
