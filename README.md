# Zero to Truck

Static site (HTML/CSS/vanilla JS) for GitHub Pages. No backend, no private keys, nothing is ever signed or sent.

## Before you publish
```
python3 scripts/setup.py --url https://YOURNAME.github.io     # social-card URLs
python3 scripts/setup.py --localize                            # fonts + hero photo into assets/ (macOS python.org: run "Install Certificates.command" once)
python3 scripts/add_csp.py                                     # strict Content-Security-Policy
python3 scripts/check_all.py --require                         # every offline safety check, one line per step
python3 scripts/update_data.py --dry-run                       # LIVE check of the chain/price APIs on your computer: read what it prints
```
Local preview: `python3 -m http.server` then open http://localhost:8000

## What is checked automatically
| Risk | Protection |
|---|---|
| Wrong / swapped QR | The page draws the QR from the shown address; `verify_qr.py` decodes every PNG and the page's generator and compares with `config.json` (CI fails on mismatch) |
| Changed address | `verify_addresses.py`: checksums, QR payload rule, unknown wallet-like strings, live-site and mirror comparison |
| Incomplete totals | `update_data.py` scans BTC, ETH (+internal transfers), USDT/USDC on Ethereum, SOL, XRP, TRX and DOGE automatically; BNB and HYPE are added by hand but their wallet balance is watched. The page shows what is automatic, by hand, incomplete or mismatched |
| Wrong ledger | key = `asset id|tx|sub`, confirmations, price + source + time per entry, append-only guard (`verify_data.py --base`), progress must equal the sum of confirmed entries |
| Money that arrived but is not counted | balance check (wallet balance vs ledger, two runs in a row) |
| Vanished transaction | recent entries re-checked on-chain; flagged for a human, never silently deleted |
| Supply chain | QR library vendored + sha256; no external JS; Actions pinned to commit SHAs; Dependabot; ledger workflow is the only writer and runs only on schedule/by hand |

## Your own test transfers
Send a small real amount to each wallet. Add its key to `tracking.exclude_tx` in `config.json` (for example `"ETH|0xabc…"`) so it is shown as `excluded` and not counted as a contribution, but still explains the balance.

## Wallets on the site
BTC, ETH, USDT (Ethereum), BNB, USDC (Ethereum), XRP, SOL, TRX, HYPE, DOGE. Every QR is drawn by the page from the displayed address. Staged but off: `USDT-BSC` (fill `track.start_block` with the BSC block height when the wallet was created) and `USDT-TRC20` (uses the TRX address). Turn one on with `"enabled": true`, then run `node scripts/gen_qr_png.js assets/qr` and `python3 scripts/check_all.py --require`. Stablecoins are accepted only for their canonical contracts. If the XRP address ever needs a destination tag, set `destination_tag` for it.

## Limits (read them)
Format checks do not prove you own an address: confirm each address in your wallet app and with a small test. Public APIs can be wrong or down; the tracker refuses to guess and says so on the page. A GitHub account takeover can change anything: keep 2FA on. Branch protection that requires pull requests also blocks the ledger bot's push (use a ruleset that exempts it, or accept manual runs).
