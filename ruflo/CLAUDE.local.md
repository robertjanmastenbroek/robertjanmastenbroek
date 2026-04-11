# RJM Command Centre — Ruflo Local Config

## Artist Identity
- **Artist:** Robert-Jan Mastenbroek / Holy Rave — "Ancient Truth. Future Sound."
- **Spotify:** `2Seaafm5k1hAuCkpdq7yds` | Instagram: @holyraveofficial (290K)
- **North Star:** 1,000,000 Spotify monthly listeners

## Brand Voice (Non-Negotiable — load `../BRAND_VOICE.md` before generating any output)
All agent output must pass 5 tests: Visualization, Falsifiability, Uniqueness, One Mississippi, Point A→B.
Subtle Salt: all content rooted in Jesus Christ's teachings — woven in, never preachy (Matt 5:13).
Compass: **"Is this how God intended it?"** — the only filter.
Banned words: blessed, anointed, curated, authentic, vibration, energy, intentional, journey.

## Queen Bee
`rjm-master` orchestrates all workers. Priority: Content → Replies → Discover → Research → Analytics.
Config: `agents/rjm-master.yaml` | Swarm: `config/rjm-swarm.json`

## Worker Fleet
| Agent | File | Cadence |
|-------|------|---------|
| holy-rave-daily-run | agents/rjm-content.yaml | Daily |
| rjm-outreach-agent | agents/rjm-outreach.yaml | Every 30min |
| rjm-discover | agents/rjm-discover.yaml | 6×/day |
| rjm-research | agents/rjm-research.yaml | 6×/day |
| rjm-playlist-discover | agents/rjm-playlist-discover.yaml | 6×/day |
| holy-rave-weekly-report | agents/rjm-analytics.yaml | Weekly |

---

# Local Development Configuration

## Environment Variables

```bash
CLAUDE_FLOW_CONFIG=./claude-flow.config.json
CLAUDE_FLOW_LOG_LEVEL=info
CLAUDE_FLOW_MEMORY_BACKEND=hybrid
CLAUDE_FLOW_MEMORY_PATH=./data/memory
CLAUDE_FLOW_MCP_PORT=3000
CLAUDE_FLOW_MCP_TRANSPORT=stdio
```

## Plugin Registry Maintenance (IPFS/Pinata)

Registry CID stored in: `v3/@claude-flow/cli/src/plugins/store/discovery.ts`
Gateway: `https://gateway.pinata.cloud/ipfs/{CID}`

Steps to add a plugin:
1. Fetch current registry: `curl -s "https://gateway.pinata.cloud/ipfs/$(grep LIVE_REGISTRY_CID v3/@claude-flow/cli/src/plugins/store/discovery.ts | cut -d"'" -f2)" > /tmp/registry.json`
2. Add plugin entry to `plugins` array, increment `totalPlugins`, update category counts
3. Upload: `curl -X POST "https://api.pinata.cloud/pinning/pinJSONToIPFS" -H "Authorization: Bearer $PINATA_JWT" -H "Content-Type: application/json" -d @/tmp/registry.json`
4. Update `LIVE_REGISTRY_CID` in discovery.ts and the `demoPluginRegistry` fallback

Security: NEVER hardcode API keys. Source from .env at runtime. NEVER commit .env.

## Doctor Health Checks

`npx claude-flow@v3alpha doctor` checks: Node 20+, npm 9+, git, config, daemon, memory DB, API keys, MCP servers, disk space, TypeScript.

## Hooks Quick Reference

```bash
npx claude-flow@v3alpha hooks pre-task --description "[task]"
npx claude-flow@v3alpha hooks post-task --task-id "[id]" --success true
npx claude-flow@v3alpha hooks session-start --session-id "[id]"
npx claude-flow@v3alpha hooks route --task "[task]"
npx claude-flow@v3alpha hooks worker list
```

## Intelligence System (RuVector)

4-step pipeline: RETRIEVE (HNSW) → JUDGE (verdicts) → DISTILL (LoRA) → CONSOLIDATE (EWC++)

Components: SONA (<0.05ms), MoE (8 experts), HNSW (150x-12,500x), Flash Attention (2.49x-7.47x)
