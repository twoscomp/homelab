# IPTV / Live TV — Setup, Decisions & Channel Lineup

Living document for the home IPTV build so work can resume across sessions/context loss.
Last updated: 2026-07-20.

> **Secrets note:** The IPTorrents playlist/EPG URLs contain an account key marked "Do Not Share."
> It is **redacted** in this file as `<USER>/<KEY>`. Real values live in the Threadfin config
> bind-mount and the IPTorrents portal — never commit the key here.

---

## 1. Architecture

**Pipeline:** IPTorrents (M3U + XMLTV) → **Threadfin** (proxy/relay/curation) → **Jellyfin** (Live TV frontend)

- **Provider:** IPTorrents VIP IPTV. **2 concurrent-stream cap** (expandable ~$8/connection).
  - Portal: `iptorrents.com/iptv` (account `twoscomp`).
  - Playlist: `https://tv123.me/iptv/<USER>/<KEY>/<config>`
  - EPG (XMLTV): `https://epg.mybunny.tv/ipt/<USER>/<KEY>/<config>`
- **Threadfin** (`fyb3roptik/threadfin`) — runs in the `media` swarm stack on **nuc8-1**, port **34400**.
  - Config bind-mount: `/mnt/dockerData/threadfin/{conf,temp}` (defined in `media.yaml`).
  - **Buffer = FFmpeg** (enables relay: many same-channel viewers → 1 upstream). **Tuners = 2** (matches provider cap).
  - **Playlist source = IPTorrents `home` config** (single source; old `threadfin` source deleted 2026-07-20).
  - **XMLTV source = `.../home` EPG** (switched from `.../threadfin`).
  - Curation: activate + EPG-map channels in the **Mapping (XEPG)** tab, then click the top **Save** (two-step; Playlist-activate alone does nothing).
  - **Config files are group-writable (`docker` grp) on nuc8-1** → bulk edits scriptable: `settings.json`
    (sources), `xepg.json` (per-channel `x-active`/`x-category`/`tvg-chno`/`x-channelID`/`x-mapping`).
    **To reload after editing (re-downloads M3U+EPG, re-ingests, PRESERVES per-channel XEPG settings keyed by
    `tvg-name`): use `docker service update --force media_threadfin`** — this is a swarm service, so do NOT use
    `docker restart <cid>` (repeated `docker restart` orphans the task and breaks ingress port routing). Backups
    in `~/threadfin-bak/` on nuc8-1. Note: from a swarm node, curling the node's own published port (`localhost:34400`
    / its own LAN IP) can hairpin-fail even when the service is healthy — verify from a real LAN client (browser/Jellyfin).
  - Outputs (LAN, not secret): M3U `http://192.168.0.101:34400/m3u/threadfin.m3u`, XMLTV `http://192.168.0.101:34400/xmltv/threadfin.xml`, UI `:34400/web`.
- **Jellyfin** (on TrueNAS `192.168.0.196:30013`, `jellyfin.whatasave.space`).
  - Tuner = **M3U Tuner** pointed at Threadfin's M3U (NOT HDHomeRun — M3U carries `tvg-id` + `tvg-logo` so logos + guide map correctly).
  - Guide = **XMLTV** provider pointed at Threadfin's XMLTV.
  - **Gotcha:** after any tuner change, **delete + re-add** the XMLTV provider so it re-maps by `tvg-id` (a plain guide refresh keeps the stale mapping → empty guide).

**Player decision:** Jellyfin over Plex — no Plex Pass/Plex Home needed, free per-user Live TV, browser web player. Plex Live TV is blocked for external shared-library users (needs their own Pass); Jellyfin has no such gate.

**Sharing (future):** relay covers many viewers of the *same* channel within the 2-stream cap; buy more connections for concurrent *different* channels. External access should use **Tailscale**, NOT the Cloudflare Tunnel (CF free tier prohibits video streaming). HEVC feeds transcode on GPU-less TrueNAS → prefer **H.264**.

---

## 2. IPTorrents configs

Each config is a named channel profile with its own M3U/EPG URL (all share the 2-stream account cap).

- `Default` — 132 US channels (original).
- `threadfin` — currently feeds Threadfin (mirrors the 132).
- `test` — scratch.
- **`<new base set>`** — the curated lineup below (build in progress).

Provider category catalog is huge (~1600+ 24/7 streams, deep international). Key international categories found:
`US Asian (89)`, `China (58)`, `Taiwan (10)`, `Japan (5)`, `South Korea (25)`, `NOW HK SPORTS (27)`,
`Malaysia (21)`, `ASTRO (36)`, `US PBS (57)`. Sports: `beIN Sports`, `DAZN`, `EPL Premier League`,
`F1 Formula`, `Fite TV` (combat/UFC), `MLS`, `Setanta`, plus league categories.

---

## 3. Curated Base Set (~125 channels) — PROPOSED

Philosophy: **comprehensive but decluttered** — keep breadth, drop `(West)` duplicates and redundant
local markets, favor H.264. Status: proposed, pending final approval + build.

### 🏈 Sports (~20)
ESPN, ESPN2, ESPNU, ESPN3, FS1, Golf Channel, Tennis Channel, MLB Network, NBA TV, NFL Network,
NHL Network, NFL RedZone, UFC Fight Pass, **beIN Sports**, an **EPL** feed, **F1 TV**, **Fite TV** (UFC/combat),
+ **UFC PPV** feeds during events. *(For family/friends; UFC specifically for friends.)*

### 📰 News (~11)
CNN, Fox News, MS Now (MSNBC), CNBC, Bloomberg, ABC News Live, BBC World News, **Al Jazeera**,
**C-SPAN 1 & 2**, Weather Channel.

### 📺 US Entertainment / Cable (~42)
A&E, AMC, Animal Planet, BBC America, BET, Bravo, Comedy Central, Discovery, Discovery ID, E!,
Food Network, Freeform, FX, FXX, Game Show Network, Hallmark, HGTV, History, IFC, Lifetime, MTV,
Nat Geo, Nat Geo Wild, Paramount Network, SYFY, TBS, TLC, TNT, Travel, TruTV, TV Land, USA Network,
VH1, WE tv, Adult Swim, Cooking Channel, Magnolia, OWN, Oxygen True Crime, Smithsonian, MeTV, CMT.
*(Dropped: all `(West)` dupes; BET Her/Jams/Soul → BET only.)*

### 🧸 Kids (~12) — toddler + baby
Disney Channel, Disney Jr, Disney XD, Nickelodeon, Nick Jr, Nicktoons, Cartoon Network, Boomerang,
PBS Kids, Baby TV, **Yoyo TV** (Taiwanese kids — bonus cultural exposure).

### 📡 Local broadcast + PBS (~7)
ABC, CBS, NBC, FOX, CW (one market — NY where available), **PBS** + a PBS variant.

### 🌏 International — Chinese / Taiwanese / HK (~16)  *(family: Taiwan + Malaysia, ethnically Chinese)*
- Chinese news/general: Phoenix North America, Phoenix InfoNews (凤凰), CCTV-4, CCTV-1, CGTN English
- Chinese entertainment/variety: CCTV-3, Dragon TV (东方卫视), Hunan Satellite TV, Zhejiang TV
- Taiwanese: TVBS Asia, ET News (東森), ET Global, ET Drama, CTS America, TTV, SETi
- Hong Kong: HOY TV

### 🌏 International — Malaysian / Japanese / Cultural (~12)  *(pick specifics during build)*
- Malaysian: marquee **Astro** channels (Ria, Prima, Awani news) + Malaysian broadcast.
- Japanese: **NHK World**, TV Japan-type.
- "Interesting sights & sounds" (English-language culture/news for browsing): NHK World, Arirang (Korea), DW, France 24.

### 🎬 Adult Animation & Anime (~8)  *(for the couple)*
Adult Swim (live; adult cartoons + Toonami anime block) + 24/7 per-show channels:
`24/7 Family Guy` (confirmed present), Rick and Morty, South Park, American Dad, Bob's Burgers, Futurama
(pull whichever exist) + any dedicated 24/7 anime. **Note:** deep anime is mostly **on-demand** (VOD),
not live — dedicated live anime series channels are limited; Adult Swim/Toonami is the main live anime.

### ➕ Recommended extra 24/7 mini-groups (proposed 2026-07-20)
- **Kids 24/7** (high value w/ toddler): Bluey, Peppa Pig, Paw Patrol, SpongeBob, Muppet Babies, My Little Pony.
- **Sitcoms 24/7** (background TV): The Office, Friends, Seinfeld, Parks & Rec, Brooklyn Nine-Nine, King of Queens, Frasier.
- **Prestige/franchise 24/7** (optional): Game of Thrones, Breaking Bad, The Sopranos; movie marathons (Harry Potter, LOTR).

---

## 4. Open Decisions

- [ ] Final approval of the base-set shape (add/cut?).
- [ ] Judgment OK on "pick-during-build" buckets (Malaysia/Astro, Japan, cultural, 24/7 cartoon/kids/sitcom titles) vs eyeball-first?
- [ ] After building: re-point Threadfin at the new config (replace the 7-ch test) or leave and switch manually?
- [ ] How many 24/7 show-channels total (tight handful vs broader)?

---

## 5. Jellyfin browsability / organization

Jellyfin Live TV has a **fixed** genre-category set (On Now / Shows / Movies / Sports / For Kids / News) —
you CANNOT add custom rows like "NFL" vs "NHL". Two levers to make the lineup browsable:

1. **EPG Category** (Threadfin, per channel) → lights up Jellyfin's broad rows. Set during mapping:
   sports channels → `Sports`, news → `News`, kids → `Kids`, movie channels → `Movie`, general → `Series`.
   *(Required because Threadfin strips the provider's genres from its output — see §1.)*
2. **Channel-number blocks + name prefixes** (Threadfin, during mapping) → makes league/group sections
   contiguous & findable in the guide. Proposed scheme:
   - `1000s` US Sports networks · `1100s` NFL · `1200s` NBA · `1300s` MLB · `1400s` NHL · `1500s` soccer/UFC/F1
   - `2000s` News · `3000s` US Entertainment · `4000s` Kids · `4500s` Adult Animation/Anime
   - `5000s` Local/PBS · `6000s` Chinese/Taiwanese/HK · `6500s` Malaysian/Japanese/cultural · `7000s` 24/7 sitcoms
   - Name prefixes like `NFL — RedZone`, `NBA — TV`, `CN — Phoenix` for at-a-glance grouping.

For true per-league *tabs*, a player app (TiviMate) honors M3U groups; Jellyfin does not.

## 6. Build Progress — `home` config (IPTorrents portal)

> **2026-07-20 reconciliation:** Pulled the live `home` M3U (`curl` the tv123 URL, count `#EXTINF`)
> and diffed it against this doc. A batch of USA-Premium/kids/locals + Taiwanese selections from the
> prior session never actually persisted (window-resize click drift). **Remediated all 25 gaps** via the
> portal — verified each by re-fetching the M3U. **Live count is now 149 channels.** The 7 24/7
> comfort-watch channels were intentionally cleared by the user (they'll curate 24/7 manually).

Selecting channels into the new `home` config via the portal. **SELECTED (149 live, verified):**
- **US Sports networks (10):** ESPN, ESPN2, FS1, NFL Net, MLB Net, NBA TV, NHL Net, Golf, CBS Sports Net, SEC Net
- **US News (11):** CNN, Fox News, MS Now, CNBC, Bloomberg, ABC News Live, BBC World, Al Jazeera, C-SPAN 1&2, Weather
- **US Entertainment/Cable + Kids + Locals + PBS (~58, from USA Premium):** A&E, AMC, Animal Planet, BBC America,
  BET, Bravo, Comedy Central, Discovery, Discovery ID, E!, Food Network, Freeform, FX, FXX, Game Show Network,
  Hallmark, HGTV, History, IFC, Lifetime, MTV, Nat Geo, Nat Geo Wild, OWN, Oxygen True Crime, Paramount, REELZ,
  Smithsonian, SYFY, TBS, TLC, TNT, Travel, TruTV, TV Land, USA, VH1, WE tv, Adult Swim, Cooking, Magnolia, MeTV, CMT
  · Kids: Disney Channel/Jr/XD, Nickelodeon, Nick Jr, Nicktoons, Cartoon Network, Boomerang, PBS Kids, Baby TV
  · Locals+PBS: ABC (Buffalo), CBS (NY), NBC (NY), FOX 29 Buffalo, CW (Philly), PBS
- **NFL (9):** NFL RedZone + out-of-market game feeds (NFL 01–05, NFL |01–03) — covers any team's games + all-action.
  NOTE: NFL category also has per-team feeds (`NFL Teams: CBS/FOX <team>`); add the SIL's specific team feed if known.
- **International Chinese/Taiwanese (19, US Asian):** CN — Phoenix InfoNews, Phoenix North America, CCTV-4,
  CCTV Entertainment, CGTN English, Dragon TV, Hunan Satellite, Zhejiang, Great Wall Elite, Beijing TV ·
  TW — ET News, ET Global, ET China, ET Drama, CTS America, TTV, SETi, CiTi TV · Yoyo TV (TW kids)

- **League game feeds:** NBA 01–08, NHL 01–08, MLB 01–08 (out-of-market slots, like NFL). League nets already in.
- **Sports add-ons:** beIN Sports (English 1&2, beIN 1&2 — soccer), Fite TV 24/7 (combat), F1 TV, EPL match feeds 01–05.
- **International — Japan (3):** Fuji TV, Gaki No Tsukai (Eng subs), **Animax Japan** (anime).
- **International — Malaysia/ASTRO (7):** RTM TV 1, Astro Awani (news), Astro Prima, Astro Ria, RTB Sukmaindera, TV 1, TV 9.

- **Remediated 2026-07-20 (persistence-failure gaps re-added, verified in live M3U):**
  · Cable/Ent (15): AMC, BBC America, Comedy Central, Discovery Channel, FX, FXX, HGTV, Lifetime, Nat Geo Wild,
    Oxygen True Crime, Paramount Network, TBS, TV Land, USA Network. **REELZ intentionally left out** (declutter).
  · Kids (4): Disney Channel, Disney XD, PBS Kids, Baby TV · Locals/PBS (2): CW (Philly), PBS
  · Taiwanese (4): ET Drama, TTV, SETi, Yoyo TV · Combat (1): **UFC Fight Pass** (US Sports).

- **24/7 comfort-watch: CLEARED by user** — the user will curate 24/7 channels manually. (Prior session's 7:
  Family Guy/King of Queens/My Name Is Earl/IT Crowd/The Jeffersons/The Jinx/Murdoch Mysteries — all removed.)
  Kids block stays strong without toddler-24/7 (Cartoon Network, Nick Jr, Disney Jr, PBS Kids, Boomerang, Baby TV, Yoyo).

- **Added on request 2026-07-20:** US: ESPNU, US: Tennis Channel, KR: Arirang (South Korea, English intl feed).
  **ESPN3 skipped** — no clean US linear channel exists (only an ambiguous HBO-MAX-grouped "(FEED)"); ESPN3 is a
  streaming-only overflow service, not a channel.
- **User's manual 24/7 curation (in progress, not managed by assistant):** e.g. Bluey, Dora the Explorer,
  Sesame Street, Dragon Ball Z, Kidulthood — user is adding these directly; leave them alone.

**BUILD COMPLETE + RECONCILED — 152 curated + user's 24/7 picks (157 live M3U, verified 2026-07-20).**
Remaining optional: US PBS extras.

Notes: Gaming/esports/programming NOT available as live TV (G4/TechTV defunct → YouTube/Twitch/VOD). Douyu skipped. Weather ✅.
UFC for friends: ESPN/ESPN2 + **UFC Fight Pass** (now confirmed in) for Fight Nights; UFC PPV events appear in PPV Live Events on fight nights.
NOW HK SPORTS + F1 driver-onboard feeds intentionally skipped (niche for this household).

Then: point Threadfin at `.../home`, delete old `threadfin`/`test` configs, and Threadfin XEPG mapping
(activation + EPG Category + numbering) — the last step left for the user's review pass.

## 7. Status Log

- **2026-07-20** — Threadfin deployed in `media` stack on nuc8-1 (512MB mem limit; buffer=FFmpeg, tuners=2).
  7-channel H.264 test curated & mapped: **ESPN, FOX News, CNN, TNT, Comedy Central, CBS (NY), NBC (NY)**.
  Wired into Jellyfin via **M3U tuner + XMLTV**; logos + EPG guide confirmed working after switching HDHomeRun→M3U
  tuner and re-adding the XMLTV provider.
- **2026-07-20** — Explored IPTorrents catalog; drafted the ~125-channel base set above (incl. international + anime). Build pending approval.
- **2026-07-20** — Reconciled live `home` config against this doc: found 124 channels (a batch of prior selections
  had silently failed to persist). Re-added 25 gap channels (USA Premium cable/kids/locals, 4 Taiwanese, UFC Fight Pass),
  verified each via M3U re-fetch. **Live count now 149.** REELZ intentionally dropped; 24/7 comfort-watch cleared (user curating manually).
- **2026-07-20** — Added ESPNU, Tennis Channel, Arirang (ESPN3 skipped — no clean linear channel). User added 5 24/7 kids
  streams. **Threadfin fully configured for `home` (157 channels):** removed old `threadfin` M3U source, pointed XMLTV at
  `home` EPG, then bulk-mapped all 157 via `xepg.json` edit — every channel **active**, **EPG-mapped** (81 with real
  tvg-id; game feeds are dummy-EPG by design), **categorized** for Jellyfin rows (Sports 53 / News 11 / Kids 15 /
  Series 78), and **numbered** by the §5 scheme (Sports 1000s, News 2000s, Ent 3000s, Kids 4000s, AdultSwim 4500s,
  Locals/PBS 5000s, CN/TW 6000s, MY/JP/KR 6500s, 24/7 7000s). Output `threadfin.m3u`/`threadfin.xml` verified.
  **Next: Jellyfin guide refresh** (M3U/XMLTV URLs unchanged, so refresh guide + rescan M3U tuner to pull the new
  channels/numbers/categories; per §1 gotcha, delete+re-add the XMLTV provider if the guide looks stale).
  Caveat: MLB game-feed channels embed the matchup in their name → they churn daily and will need periodic re-activation;
  NBA/NHL "01–08" slots and all fixed channels persist across the nightly (00:00) auto-update.
