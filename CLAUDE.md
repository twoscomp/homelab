# Claude Code — Project Instructions

Full operational SOP and infrastructure context is in [`AGENTS.md`](./AGENTS.md). Read it before making changes.

## Key Conventions

- **Deploy stacks** with `docker-compose -f <file>.yaml config | docker stack deploy -c - <stackname>` — docker-compose v1 only, not `docker compose` v2. Stack deploy does not read `.env` natively; docker-compose is the pre-processor.
- **Run stack commands on nuc8-1** (`ssh nuc8-1`). nuc8-2 is a worker — stack deploys must go through the manager.
- **Sync repo to nuc8-2** with `rsync -a --delete /home/dlin/smarthomeserver/ nuc8-2:/home/dlin/smarthomeserver/` (run from nuc8-1). nuc8-2 has no GitHub SSH key and doesn't need one — all deploys go through nuc8-1; rsync keeps nuc8-2's keepalived/system configs current. Do this after every `git push`.
- **Never hardcode secrets** in yaml files. All credentials go in `.env` (gitignored) and are referenced as `${VAR_NAME}`.
- **Swarm DNS inside overlay:** `<stack>_<service>` e.g. `media_sonarr`, `tier1_nginx-proxy-manager`.
- **Use `wget` not `curl` in Alpine containers** for any URL that resolves via Swarm DNS. Alpine curl uses c-ares which cannot query Docker's embedded resolver at `127.0.0.11`. wget uses musl libc and works correctly.

## Uptime Kuma (fly.io)

- Access DB: `flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"<query>\""`
- When inserting monitors directly via SQL: **always set `user_id=1`** or the monitor is silently hidden.
- When creating group monitors via SQL: **clone a full existing group row** (`INSERT INTO monitor SELECT NULL, 'Name', ... FROM monitor WHERE id=<group_id>`) — minimal inserts produce broken groups.
- After any direct DB change: `flyctl app restart dlin-uptime-kuma`.

## Ops Log

Update `ops-log.md` in the repo root after any infrastructure change or ops review finding. See `AGENTS.md` §7 for the full ops review SOP and Kuma queries.
