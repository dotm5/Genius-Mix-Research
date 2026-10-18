# Genius Mix Research: Data Format, Constraint Chain and Offline Tooling

**English** | [简体中文](README.md)

[![CI](https://github.com/dotm5/Genius-Mix-Research/actions/workflows/ci.yml/badge.svg)](https://github.com/dotm5/Genius-Mix-Research/actions/workflows/ci.yml)

A study of the iTunes Genius Mix pipeline on the iPod nano (5th and 6th generation). The repository documents the byte-level layout of the device `.itdb` databases, the Genius constraint-chain evaluation semantics, the cluster adjacency graph, the Mix state-machine thresholds and playlist gating, and ships a toolchain that depends only on the Python standard library. The discovery path at the end records how the results converged from container enumeration to bytecode ablation.

Genius has four independent data planes: the Kind-32 container holds candidate seeds, `genius_similarities` supplies candidate edges, `genius_metadata` stores the constraint keys, and `genius_config.data` carries the per-constraint bytecode. A Genius Mix is usable at `count >= 2` (target 10 tracks); a per-track playlist defaults to a target of 25 and a minimum of 10.

The 5th and 6th generation carry the same 151,212-byte `genius_config.data`, and their schemas differ by a single column in `Dynamic.itdb.item_stats`. The itemised comparison is in [cross-generation validation](docs/CROSSGEN.md).

The reference documents under `docs/` are written in Chinese.

## Mechanism overview

```text
Per-track Genius playlist                  Genius Mix
current track valid, genius_id nonzero     Kind-32 container walker
            |                             item_to_container by physical_order
            |                             skip item.genius_id == 0
            |                                      |
            |                       LCG seed sampler (Primary gate)
            |                       n=0 fails; n=1 takes the only seed
            |                       n>1 samples without replacement, at most min(n,100)
            |                       primary set only when j=0 succeeds
            |                       later successes go to extras, no backfill
            +----------------------+---------------+
                                   |
                 Mix track entry (NextMixTrack)
                                   |
                    shared core for graph walk and playlist build
                                   |
            genius_metadata seed lookup: missing row or empty data fails
              32B baseline = 4 little-endian u64 constraint keys, indexed by field
                                   |
        genius_similarities: read candidate genius_id -> expand, dedupe, load metadata
                                   |
           Config constraint bytecode evaluator
           op=2 cluster adjacency whitelist / op=1,3 same-key spacing / op=4 skip stats
           op=5 random jitter; returns 0 accept, 1 defer, 2 reject and mark seen
                                   |
                   accept track -> update constraint state -> keep expanding
                                   |
           Mix: target=10 / minimum=2; per-track playlist: default 25 / 10
           stop at target; usable when count >= minimum and the object is valid
```

The repair tool writes four valid `genius_id` values at same-container strides 1/5/9/13, producing a 32B row of constraint keys. The construction is named the same-container dense connected graph in early notes; the third decoding round established that these slots are constraint keys and that candidate reachability comes from `genius_similarities`. Each node carries at most four references, and small containers may repeat a key.

## Ablation

| Variant | Entries kept | Size | Final result |
|---|---|---:|---|
| `cfg_empty` | none | 48 B | usable |
| `cfg_jitter` | `#6` random_jitter | 52 B | usable |
| `cfg_limits` | `#2/#3` distance, `#4` skip_count | 108 B | usable |
| `cfg_notable` | `#1..#6`, no large table | 120 B | usable |
| `cfg_tableonly` | `#0` 591-entry table | 151,140 B | fails |

`cfg_tableonly` (the 591-entry table alone) fails on the offline model. The device baseline (rounds 11-16) puts admission on the `op=2` entry: a candidate whose label hits the cluster table passes, a miss returns 2, and the entry absent from the config leaves the constraint uninstantiated. Neither the metadata length nor the `field=20` key changes playability, see the [cross-generation note](docs/CROSSGEN.md) §2. `cfg_limits` (108B) is the compact option, keeping two distance constraints and one skip_count.

## Original config path

The unmodified 151,212B config runs on both generations, carried by the 168B (21 u64 words) layout of `genius_metadata.data`: entry 7 then reads a real per-track key for `field=20`, so the spacing constraint applies per track. With the 32B layout that entry reads key 0, and playability follows the `op=2` hit instead of the layout length ([cross-generation note](docs/CROSSGEN.md) §2).

Filling `metadata[0]` with an id from that table makes the `op=2` adjacency whitelist converge candidates by genre. The table has 33 connected components, several of them **complete graphs**: assigning one family of genres to different clusters inside the same component keeps the family mutually adjacent and filters across families. Candidates come from `genius_similarities` through a **library-level** pool; narrowing to container level leaves small containers short of candidates.

Full procedure, parameters and validation are in the [alignment document](docs/ALIGN.md); device boundary measurements in the [boundary record](docs/BOUNDARY.md), the per-round device log in the [device test rounds](docs/ROUNDS.md), replay gain in the [gain note](docs/REPLAYGAIN.md), Mix naming and localization in the [naming note](docs/NAMING.md), and the device-level differences in the [cross-generation note](docs/CROSSGEN.md) (all Chinese).

## Quick start

Python 3.9+, standard library only. Quit iTunes and other sync software first. The tool rewrites constraint keys and config in place, on existing Kind-32 containers with existing non-empty similarities.

```powershell
$dbDir = Read-Host 'iTunes Library.itlp directory'
$backupDir = Read-Host 'private backup directory outside the repository'
python tools/apply_genius_mix_fix.py "$dbDir" --backup-dir "$backupDir"
```

The tool validates its input, snapshots every `.itdb` through the SQLite backup interface, then rewrites the `Genius.itdb` metadata and the `id=1` config inside a single transaction. The default config is the 108B `cfg_limits`; `--config empty` selects the 48B empty variant. No external binaries.

**Input baseline:**

- Kind-32 containers, each Mix holding at least two distinct nonzero `genius_id` values
- members with metadata and non-empty similarities, with no member shared between Mixes
- `Dynamic.itdb.item_stats.genius_id` matching `Library.itdb.item.genius_id`
- evaluation over the existing similarities graph; new reachability confirmed by playback

Eject safely and restart the iPod, then check playback. Sync software overwrites offline edits. To restore, quit the sync software and copy the database files from the backup directory back into the library directory.

## Tools

| File | Purpose |
|---|---|
| `tools/apply_genius_mix_fix.py` | compatible option: keep containers, write 32B constraint keys and 48B / 108B configs |
| `tools/cluster_align.py` | original-config option: cluster alignment, 168B metadata, library-level similarities |
| `tools/cluster_graph.py` | decodes the `op=2` cluster table and reports connected and complete components |
| `tools/custom_graph.py` | custom clustering: writes the first metadata slot and the similarities from a spec, then checks label adjacency per container |
| `tools/genius_sim.py` | offline simulator: replays the generation pipeline for pre-deployment checks |
| `tools/deploy_aligned.py` | deployment: snapshot backup, input validation, write |
| `tools/boundary_matrix.py` | boundary fixture: synthesizes Kind-32 containers from a spec |

All tools depend only on the Python standard library; `genius_sim.py` and `cluster_align.py` run fully offline.

## Discovery path

1. **Locate containers**: enumerate Genius Mix containers in `Library.itdb` by `container.distinguished_kind=32`, then read members and `physical_order` from `item_to_container`.
2. **Converge on the call site**: follow the `genius_metadata` lookups and `genius_config.data` reads to the Mix walker and the `NextMixTrack` executor.
3. **Decode the bytecode**: parse `data` with opcode-determined lengths to obtain op=1/3 distance, op=2 cluster adjacency, op=4 skip statistics and op=5 random jitter, and reconstruct the seven entries and control tail of the 151,212B original config.
4. **Bisect the config**: hold containers and similarities fixed, replace only `data`, and obtain 48B / 52B / 108B / 120B usable against 151,140B failing.
5. **Isolate the conflict**: the offline model converges the failure on `op=1 field=20` reading key 0 from the 32B metadata.
6. **Ship the repair**: write 32B constraint keys at same-container strides 1/5/9/13, with SQLite snapshot backup and a single write transaction.
7. **Decode the cluster table**: the `op=2` adjacency table is an undirected graph, all 591 nodes carry self-loops, and several components are complete graphs; mapping genres into distinct clusters of one component reproduces factory semantics.
8. **Correct the failure point**: offline isolation placed the failure at `op=1 field=20` reading past the 32B metadata. Device-side single-variable runs show the original seven entries usable with both 32B and 168B metadata, with admission decided by whether `op=2` hits the cluster table.
9. **Measure the boundaries**: containers synthesized with 1 … 189 members across 1 … 7 genres confirm that container count, member count and genre spread carry no device limit; an empty container appears in the list yet stays unplayable, and duplicate member rows have no effect.

## Scope

This project covers the iPod nano Genius database structures and integer layouts, together with the repair, alignment, clustering and simulation tools and the specification text. The simulated results come from offline analysis; device results follow the measured records. Contribution guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md); security reports go through [SECURITY.md](SECURITY.md).
