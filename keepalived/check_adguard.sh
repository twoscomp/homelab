#!/bin/bash
# Health check script for AdGuard Home
# Returns 0 if AdGuard is responding to DNS queries, non-zero otherwise
#
# Uses dig with +time=2 +tries=1 to fail fast (2s max) instead of nslookup's
# default ~15s timeout, which caused keepalived script timeouts under memory pressure.

dig @127.0.0.1 +time=2 +tries=1 google.com A > /dev/null 2>&1
