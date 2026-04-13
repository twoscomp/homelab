# Heartbeat-Pusher False Positive Investigation

**Date:** 2026-04-07  
**Issue:** All push monitors failing simultaneously while services remain up  
**Root Cause:** Need to investigate

---

## Current Heartbeat-Pusher Implementation

```bash
while true; do
  for pair in $$(echo "$$KUMA_MONITORS" | tr ',' '\n'); do
    check_url="$${pair%%|*}"
    push_url="$${pair##*|}"
    if wget -qO /dev/null --timeout=5 "$$check_url" 2>/dev/null; then
      wget -qO /dev/null --timeout=10 "$$push_url?status=up&msg=OK" 2>/dev/null
    else
      wget -qO /dev/null --timeout=10 "$$push_url?status=down&msg=offline" 2>/dev/null
    fi
  done
  sleep 30
done
```

**Issues Identified:**
1. **No error handling** for wget failures
2. **No logging** to understand when failures occur
3. **No retry logic** for push failures
4. **All monitors checked together** - if one fails, all might appear down
5. **No health check** on heartbeat-pusher itself
6. **No distinction** between check failure vs push failure

---

## Potential Causes of Simultaneous Failures

### 1. Docker Swarm DNS Resolution Issues
- **Symptom:** All services appear down at once
- **Cause:** Overlay network DNS (127.0.0.11) failing to resolve service names
- **Test:** Check if `docker network inspect smarthomeserver` shows proper DNS
- **Fix:** Ensure Swarm network is healthy

### 2. Resource Constraints on nuc8-1
- **Symptom:** Heartbeat-pusher restarting or running slow
- **Cause:** Memory pressure, CPU constraints
- **Test:** `docker stats heartbeat-pusher`
- **Fix:** Increase resource limits or pin to node

### 3. NPM Certificate Issues
- **Symptom:** NPM services showing as down
- **Cause:** Certbot attempting renewal for deleted certs
- **Fix:** Already resolved (orphaned certs removed)

### 4. Network Connectivity Issues
- **Symptom:** Push URLs unreachable
- **Cause:** Firewall rules, cloudflared issues
- **Test:** Test push URLs from within Docker Swarm
- **Fix:** Verify cloudflared connectivity

### 5. Keepalived VIP Failover
- **Symptom:** AdGuard VIPs showing as down
- **Cause:** One node down, VIP not routing correctly
- **Fix:** Verify keepalived configuration

---

## Investigation Plan

### Phase 1: Add Logging to Heartbeat-Pusher
Create a new image or modify the current one to add logging.

### Phase 2: Check Swarm DNS Resolution
Verify that service names resolve correctly from within the heartbeat-pusher container.

### Phase 3: Resource Monitoring
Monitor CPU and memory usage of heartbeat-pusher during failure windows.

### Phase 4: Network Path Verification
Test connectivity from heartbeat-pusher to:
- Internal services (via Swarm DNS)
- External push URLs (via cloudflared)

### Phase 5: Implement Graceful Degradation
If push URLs fail, don't mark services as down - only mark down if check URLs fail.

---

## Immediate Fixes to Implement

### 1. Add Logging to Heartbeat-Pusher

Create `heartbeat-pusher-debug.yaml`:

```yaml
services:
  heartbeat-pusher:
    image: alpine:3.19
    environment:
      - TZ=${TZ}
      - KUMA_DEBUG=${KUMA_DEBUG:-false}
    command:
      - sh
      - -c
      - |
        while true; do
          echo "$(date -Iseconds): Starting heartbeat cycle" >> /logs/heartbeat.log
          for pair in $$(echo "$$KUMA_MONITORS" | tr ',' '\n'); do
            check_url="$${pair%%|*}"
            push_url="$${pair##*|}"
            echo "$(date -Iseconds): Checking $$check_url" >> /logs/heartbeat.log
            
            # Add timeout check to prevent hanging
            if ! timeout 5 wget -qO /dev/null "$$check_url" 2>&1 | tee -a /logs/heartbeat.log; then
              echo "$(date -Iseconds): Check failed for $$check_url" >> /logs/heartbeat.log
              if timeout 10 wget -qO /dev/null "$$push_url?status=down" 2>&1 | tee -a /logs/heartbeat.log; then
                echo "$(date -Iseconds): Pushed down status for $$check_url" >> /logs/heartbeat.log
              else
                echo "$(date -Iseconds): Push failed for $$check_url" >> /logs/heartbeat.log
              fi
            else
              echo "$(date -Iseconds): Check passed for $$check_url" >> /logs/heartbeat.log
              if timeout 10 wget -qO /dev/null "$$push_url?status=up" 2>&1 | tee -a /logs/heartbeat.log; then
                echo "$(date -Iseconds): Pushed up status for $$check_url" >> /logs/heartbeat.log
              else
                echo "$(date -Iseconds): Push failed for $$check_url (service up, push down)" >> /logs/heartbeat.log
              fi
            fi
          done
          sleep 30
        done
    volumes:
      - /logs:/logs
```

### 2. Add Health Check for Heartbeat-Pusher

```yaml
healthcheck:
  test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://localhost/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

### 3. Create Health Endpoint in heartbeat-pusher

Add a simple `/health` endpoint that returns 200 OK.

---

## Recommended Next Steps

1. **Deploy heartbeat-pusher with logging** to identify the root cause
2. **Check Docker Swarm DNS** resolution from within the heartbeat-pusher
3. **Monitor resource usage** during failure windows
4. **Review push URL configuration** to ensure they're reachable
5. **Consider separating** push failures from check failures
6. **Implement alerting** specifically for heartbeat-pusher restarts

---

## Monitoring Commands

```bash
# Check heartbeat-pusher status
docker ps -f name=heartbeat-pusher

# View heartbeat-pusher logs
docker logs --tail 100 heartbeat-pusher

# Check Swarm DNS resolution
docker exec <heartbeat-pusher> ping media_sonarr

# Check resource usage
docker stats --no-stream heartbeat-pusher

# View fly.io logs
flyctl logs -a dlin-uptime-kuma

# Check for restarts
docker history heartbeat-pusher
```

---

**Note:** The DOMAIN_EXPIRY warnings in Fly.io logs are harmless - they're notifications about expired monitor dates in whatasave.space, not actual issues with the monitoring itself.
