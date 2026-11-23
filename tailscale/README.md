# Tailscale subnet router (nuc8-2)

Lets remote tailnet devices (Steam Deck, phones) reach the home LAN `192.168.0.0/24`, including the PS5s for chiaki-ng Remote Play and wake. Set up 2026-11-22.

## Why nuc8-2, on the host

- **nuc8-2** is the swarm worker, so the router adds the least risk there. nuc8-1 is the swarm leader, runs NPM on host ports, holds DNS VIP .253, and its CrowdSec firewall bouncer rewrites iptables.
- **Not TrueNAS or the HAOS add-on:** both depend on the TrueNAS e1000e NIC that hangs, and TrueNAS RAM is nearly full. **Not the UDM Pro:** UniFi OS doesn't support Tailscale, and firmware updates wipe community installs.
- **Not a swarm service:** swarm can't pass `/dev/net/tun`, which forces slow userspace networking (bad for streaming UDP). It would also die with Docker; Docker on nuc8-2 failed to start after a power blip before.

## What `setup.sh` does (idempotent)

1. Installs `tailscale` from the official apt repo (`pkgs.tailscale.com`, noble) and enables `tailscaled`.
2. Writes `/etc/sysctl.d/99-tailscale.conf` with `net.ipv4.ip_forward = 1`. Docker only sets forwarding when it starts, so without this, routing would stop whenever Docker fails to start.
   - IPv6 forwarding is deliberately off: the LAN has no IPv6 and only an IPv4 route is advertised. `tailscale up` prints an "IPv6 forwarding is disabled" warning; that's expected.
3. `tailscale-gro.service` (oneshot, at boot) runs `ethtool -K eno1 rx-udp-gro-forwarding on rx-gro-list off`. This is Tailscale's recommended throughput tuning for Linux subnet routers.

It does not sign in. Sign in with the command below. It prints the login link; the hostname and route are already saved, so re-running it is harmless.

```bash
ssh nuc8-2 'sudo tailscale up --hostname=nuc8-2 --advertise-routes=192.168.0.0/24'
```

SNAT stays on (the default): LAN devices see traffic from 192.168.0.26, so the UDM needs no static route back to `100.64.0.0/10`.

## Admin console (login.tailscale.com), after signing in

1. **Approve the machine**, if device approval is on.
2. **Approve the route:** Machines → nuc8-2 → ⋯ → *Edit route settings* → tick `192.168.0.0/24`. It does nothing until it's approved.
3. **Disable key expiry:** same ⋯ menu. Otherwise the router logs out after about 180 days.
4. **Access controls:** the default allow-all policy needs no change. With custom grants, the user's devices need `192.168.0.0/24`.
5. Optional: split DNS `swarm.localdomain` → 192.168.0.253, 192.168.0.254.

## Verify (after the route is approved)

On nuc8-2:
```bash
tailscale status                        # nuc8-2 online, no "Log in at"
sudo iptables -S ts-forward             # tailscale's FORWARD rules (Docker's FORWARD policy is DROP)
sudo iptables -S FORWARD | head -4      # FORWARD should jump to ts-forward
```
Then, from a remote device on cellular:
1. Run `tailscale ping nuc8-2`. It should say direct, not "via DERP". If it stays on DERP, add a UDP 41641 port forward on the UDM → 192.168.0.26.
2. `ping 192.168.0.180` (PS5-177).
3. Test Docker interplay: `sudo systemctl restart docker` on nuc8-2, then ping again.

## Clients

- **Android/iOS:** the Tailscale app accepts subnet routes automatically.
- **Steam Deck (SteamOS):** install Tailscale via the community `deck-tailscale` (systemd-sysext). Linux needs **`sudo tailscale up --accept-routes`**, because routes are off by default there.
- **chiaki-ng:** broadcast discovery doesn't cross the tunnel, so add each PS5 manually by IP. Do the one-time registration (PSN account ID + PIN from *Settings → System → Remote Play → Link Device*) at home.
- **Wake:** chiaki-ng sends a unicast wake to the PS5 on port 9302, so it routes fine. Each PS5 needs *Settings → System → Power Saving → Features Available in Rest Mode* → **Stay Connected to the Internet** + **Enable Turning On PS5 from Network**.
- **PS5 IPs:** PS5-177 = `192.168.0.180` (found by Remote Play discovery 2026-11-22). The second PS5 is on the main LAN but was off. Give both a DHCP reservation in UniFi.

## Caveats

- **Subnet clash:** a remote network that also uses 192.168.0.0/24 (common at friends' houses and hotels) wins over the route, so home is unreachable from there.
- **Single router:** for failover, run the same setup on nuc8-1 advertising the same route. Check that the Tailscale plan includes subnet-router failover first.

## Rollback

```bash
ssh nuc8-2 'sudo tailscale down; sudo systemctl disable --now tailscale-gro; sudo rm /etc/systemd/system/tailscale-gro.service /etc/sysctl.d/99-tailscale.conf; sudo apt-get remove -y tailscale'
```
Then delete the machine in the admin console. Keep `net.ipv4.ip_forward` at 1: Docker needs it.
