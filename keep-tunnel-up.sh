#!/bin/bash
# Tunnel watchdog — restart cloudflared if it dies
TUNNEL_PID=$(pgrep -f "cloudflared tunnel" | head -1)
if [ -z "$TUNNEL_PID" ]; then
  echo "$(date): Tunnel not running, restarting..." >> /tmp/tunnel-watchdog.log
  nohup cloudflared tunnel --url http://localhost:5000 > /tmp/cloudflared.log 2>&1 &
  echo "$(date): Started tunnel PID $!" >> /tmp/tunnel-watchdog.log
fi
