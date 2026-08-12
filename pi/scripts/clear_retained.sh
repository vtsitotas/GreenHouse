#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — delete retained MQTT topics, on BOTH brokers, in the only
# order that actually works.
# ═══════════════════════════════════════════════════════════════════════════
# Why this exists: retained messages outlive the hardware that published them.
# A sensor that was rewired, renamed or retired keeps showing up in the app
# forever -- as a phantom device, and (for a reading topic) as a phantom zone
# on the dashboard -- because MQTT replays retained topics in full to every
# client that connects. Deleting the hardware does nothing; the broker still
# holds the last message it ever sent.
#
# Two traps make this much harder than "publish an empty payload". Both were
# hit for real on 2026-08-12, chasing a ghost `zone1_test` node that survived
# three separate clean-ups:
#
#   1. THERE ARE TWO BROKERS, AND A DELETION DOES NOT CROSS BETWEEN THEM.
#      hivemq_bridge.py forwards `greenhouse/#` both ways, but MQTT delivers a
#      *live* message to an existing subscriber with the retain flag CLEARED
#      (it is only set when a message is replayed to a new subscription). The
#      bridge forwards `retain=msg.retain`, so a retained publish -- including
#      the empty-payload publish that means "delete" -- crosses the bridge as
#      an ordinary, non-retained message. The other broker's stored copy is
#      left completely untouched.
#
#      Retained STATE therefore only syncs when the bridge (re)connects and
#      receives the other side's retained set with retain=1. That is what
#      resurrected the ghosts: local was cleared, the bridge later reconnected,
#      pulled the cloud's still-dirty retained set down, and republished it
#      locally *with* retain. Verified by planting a topic on the cloud only:
#      it did not appear locally for minutes, then appeared the instant the
#      bridge was restarted.
#
#      So both brokers must be cleared explicitly. Cloud first, then local,
#      so a reconnect during the run can only ever re-seed from an
#      already-clean source.
#
#   2. MOSQUITTO DOESN'T WRITE THE DELETION TO DISK STRAIGHT AWAY. With
#      `persistence true` and no `autosave_interval` set (this project's
#      config), the default is 1800s -- so a power cut within 30 minutes of a
#      clean-up silently restores every ghost on the next boot. SIGUSR1
#      forces the persistence file out immediately.
#
# Usage (on the Pi):
#     sudo bash /home/pi/greenhouse/scripts/clear_retained.sh 'greenhouse/nodes/AABBCCDDEEFF/#'
#     sudo bash /home/pi/greenhouse/scripts/clear_retained.sh 'greenhouse/zone1_test/#'
#
# Multiple patterns are allowed. Wildcards are expanded against what the
# brokers actually hold -- you cannot delete a topic by publishing to a
# wildcard, so this discovers the concrete topics first.
#
#     DRY_RUN=1 sudo bash .../clear_retained.sh 'greenhouse/#'   # list only
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

[ $# -ge 1 ] || {
  echo "Usage: $0 <topic-pattern> [more patterns...]" >&2
  echo "       DRY_RUN=1 $0 'greenhouse/#'   # show what would be deleted" >&2
  exit 1
}

DRY_RUN="${DRY_RUN:-0}"
HIVEMQ_CFG=/etc/greenhouse/hivemq.json
LOCAL_HOST=127.0.0.1
LOCAL_PORT=1883
# Long enough for a broker to finish replaying its retained set, short enough
# not to hang a maintenance script. Retained delivery is immediate on connect;
# this is only waiting out the tail.
DISCOVER_SECS=6

# ── Cloud credentials (optional: a unit with no remote access is fine) ──────
CLOUD_HOST=""; CLOUD_PORT=""; CLOUD_USER=""; CLOUD_PASS=""
if [ -f "$HIVEMQ_CFG" ]; then
  CLOUD_HOST=$(python3 -c "import json;print(json.load(open('$HIVEMQ_CFG')).get('host',''))" 2>/dev/null || true)
  CLOUD_PORT=$(python3 -c "import json;print(json.load(open('$HIVEMQ_CFG')).get('port',8883))" 2>/dev/null || true)
  CLOUD_USER=$(python3 -c "import json;print(json.load(open('$HIVEMQ_CFG')).get('username',''))" 2>/dev/null || true)
  CLOUD_PASS=$(python3 -c "import json;print(json.load(open('$HIVEMQ_CFG')).get('password',''))" 2>/dev/null || true)
fi
# A placeholder host (the shipped .example) is not a configured cloud broker.
case "$CLOUD_HOST" in
  ""|your-cluster-id*) CLOUD_HOST="" ;;
esac

have_cloud() { [ -n "$CLOUD_HOST" ] && [ -n "$CLOUD_USER" ]; }

# List the concrete retained topics currently matching the given patterns.
# `-v` prints "topic payload"; we keep only the topic. An empty payload is
# never emitted by a broker (that IS the deletion), so anything listed here
# genuinely still exists.
discover_local() {
  timeout "$DISCOVER_SECS" mosquitto_sub -h "$LOCAL_HOST" -p "$LOCAL_PORT" \
    "${PATTERN_ARGS[@]}" -v --retained-only 2>/dev/null | awk '{print $1}' | sort -u || true
}

discover_cloud() {
  # No --retained-only here: this is a fresh subscription to a broker nothing
  # else is publishing to on our behalf, so everything arriving in the window
  # is by definition retained state.
  timeout "$DISCOVER_SECS" mosquitto_sub -h "$CLOUD_HOST" -p "${CLOUD_PORT:-8883}" \
    --capath /etc/ssl/certs -u "$CLOUD_USER" -P "$CLOUD_PASS" \
    "${PATTERN_ARGS[@]}" -v 2>/dev/null | awk '{print $1}' | sort -u || true
}

PATTERN_ARGS=()
for pattern in "$@"; do
  PATTERN_ARGS+=(-t "$pattern")
done

echo "Patterns: $*"
echo

echo "── Discovering retained topics ─────────────────────────────────────────"
LOCAL_TOPICS=$(discover_local)
CLOUD_TOPICS=""
if have_cloud; then
  CLOUD_TOPICS=$(discover_cloud)
else
  echo "(no HiveMQ Cloud configured — local broker only)"
fi

ALL_TOPICS=$(printf '%s\n%s\n' "$LOCAL_TOPICS" "$CLOUD_TOPICS" | grep -v '^$' | sort -u || true)

if [ -z "$ALL_TOPICS" ]; then
  echo "Nothing retained matches. Nothing to do."
  exit 0
fi

echo "$ALL_TOPICS" | sed 's/^/  /'
COUNT=$(echo "$ALL_TOPICS" | wc -l)
echo
echo "$COUNT topic(s) would be deleted from both brokers."

if [ "$DRY_RUN" != "0" ]; then
  echo "DRY_RUN set — nothing deleted."
  exit 0
fi

# ── 1. Cloud FIRST ─────────────────────────────────────────────────────────
# Order is not cosmetic. The empty-payload delete published below reaches the
# other broker only as a NON-retained message (see the header), so it will not
# delete anything there -- and if the bridge reconnects mid-run it re-seeds
# whichever side is still dirty. Clearing the cloud first means a reconnect at
# the worst possible moment can only pull down an already-clean set.
if have_cloud; then
  echo
  echo "── Clearing HiveMQ Cloud (first, so it can't re-seed local) ─────────"
  while IFS= read -r topic; do
    [ -n "$topic" ] || continue
    mosquitto_pub -h "$CLOUD_HOST" -p "${CLOUD_PORT:-8883}" --capath /etc/ssl/certs \
      -u "$CLOUD_USER" -P "$CLOUD_PASS" -t "$topic" -r -n && echo "  cleared: $topic"
  done <<< "$ALL_TOPICS"
fi

# ── 2. Then local ──────────────────────────────────────────────────────────
echo
echo "── Clearing local Mosquitto ────────────────────────────────────────────"
while IFS= read -r topic; do
  [ -n "$topic" ] || continue
  mosquitto_pub -h "$LOCAL_HOST" -p "$LOCAL_PORT" -t "$topic" -r -n && echo "  cleared: $topic"
done <<< "$ALL_TOPICS"

# ── 3. Force the deletion to disk ──────────────────────────────────────────
echo
if pkill -USR1 -x mosquitto 2>/dev/null; then
  echo "Asked Mosquitto to write its persistence file now (SIGUSR1)."
  sleep 2
else
  echo "WARNING: could not signal mosquitto — the deletion is in memory only," >&2
  echo "         and a power cut before the next autosave will undo it." >&2
fi

# ── 4. Verify, rather than assume ──────────────────────────────────────────
echo
echo "── Verifying ───────────────────────────────────────────────────────────"
sleep 3   # give the bridge a chance to mirror anything it was going to
REMAIN_LOCAL=$(discover_local)
REMAIN_CLOUD=""
have_cloud && REMAIN_CLOUD=$(discover_cloud)
REMAIN=$(printf '%s\n%s\n' "$REMAIN_LOCAL" "$REMAIN_CLOUD" | grep -v '^$' | sort -u || true)

if [ -z "$REMAIN" ]; then
  echo "Clean on both brokers."
else
  echo "STILL PRESENT after clearing:" >&2
  echo "$REMAIN" | sed 's/^/  /' >&2
  echo >&2
  echo "Something is republishing these — check that the device really is" >&2
  echo "retired (serial_bridge.py republishes for any node the bridge still" >&2
  echo "hears) before assuming the deletion failed." >&2
  exit 1
fi
