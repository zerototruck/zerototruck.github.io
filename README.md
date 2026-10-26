# Zero to Truck: Project Details & Policy

**Website: https://zerototruck.github.io/**

Last updated: 26 October 2026. If this document differs from the website, the website and the public ledger are the record.

## What Zero to Truck is

Zero to Truck is a public internet experiment that asks one question: can the internet help turn a public blockchain wallet into a real, working truck? It is not a donation box, not a charity and not an investment.

The target is one Volvo FH500 (model year 2022) tractor unit with a trailer. Everything is public by design: the wallet addresses, the ledger of received transactions, the purchase proof and the change log. The experiment is run by a private individual who has chosen to stay anonymous, so the project asks you to check the receipts, not to trust a name.

## The goal

The public goal is fixed at **$130,000**: $100,000 for the Volvo FH500 (2022) tractor unit and $30,000 for the trailer. The number does not move. If the plan ever changes, the change is written in the public change log instead of quietly edited.

Progress is shown as **Verified Raised**, a USD figure built only from confirmed on-chain transactions in the public ledger. It starts at $0, and the page says so plainly until real transactions exist. Milestones are marked at 25%, 50%, 75% and 100%.

## How contributing works

Sending takes six steps, and the first three decide whether the money arrives.

1. **Choose an asset** on the website.
2. **Verify the network.** The same asset can exist on several networks (USDT lives on Ethereum, Tron and BNB Smart Chain). The wallet card names the exact network to use.
3. **Verify the address.** Copy it or scan the QR code, then compare the last six characters shown after copying with what your wallet displays.
4. **Send from your own wallet.** You pay the network fee.
5. **Check the blockchain.** Use the explorer button next to the address, or paste your transaction hash into any block explorer.
6. **Wait for confirmations.** After enough confirmations the transaction is recorded in the public ledger.

Crypto transfers are irreversible. A wrong asset, network or address cannot be undone by anyone.

## Accepted assets and networks

The site accepts eleven wallet entries on seven networks. Only these assets, on these networks, are counted.

| Asset | Network | How it is recorded |
| --- | --- | --- |
| BTC | Bitcoin | Automatic |
| ETH | Ethereum | Automatic, including transfers sent by smart contracts |
| USDT | Ethereum (ERC-20) | Automatic, official Tether contract only |
| USDT | Tron (TRC-20) | Automatic, official Tether contract only |
| USDT | BNB Smart Chain (BEP-20) | Automatic, official Tether contract only |
| BNB | BNB Smart Chain (BEP-20) | Added by hand; wallet balance is watched |
| USDC | Ethereum (ERC-20) | Automatic, official Circle contract only |
| XRP | XRP Ledger | Automatic |
| SOL | Solana | Automatic, native SOL only |
| TRX | Tron | Automatic |
| DOGE | Dogecoin | Automatic |

Some networks share an address. ETH, USDT (ERC-20 and BEP-20), BNB and USDC use one Ethereum-style address, and TRX and USDT (TRC-20) use one Tron address. That is normal for these networks, but it means the network you pick in your wallet is the only thing separating them. Any other token, and any look-alike token with the same name, is not counted. If a destination tag is ever shown for XRP, it must be included.

## Transparency and verification

Every number on the site has a receipt. The ledger is a public file in this repository (`data/ledger.json`), and the tracker and its tests live in the same repository, so anyone can read exactly how entries are made.

- **Only confirmed transactions count.** An entry is recorded after enough confirmations: 3 on Bitcoin, 12 on Ethereum, 15 on BNB Smart Chain, 20 on Dogecoin, and finalized, validated or solidified status on Solana, XRP Ledger and Tron.
- **Every entry shows its price.** The USD value is the coin amount times the market price when the entry was recorded. The price, its source and the time are stored with the entry. Prices come from CoinGecko and are cross-checked against Coinbase; if they disagree by more than 5%, the tracker refuses to guess. Stablecoins are valued at their $1 peg.
- **The ledger is append-only.** New entries can be added. Existing entries cannot be edited or removed; only an entry's status and note can change, and a status change needs a written note.
- **Statuses are explicit.** Only *confirmed* entries add to Verified Raised. *Dust* (under $1), *excluded* (for example the operator's own test transfers) and *disputed* entries are shown but not counted.
- **Missing money is flagged.** The tracker compares each wallet's real balance with the ledger. If a wallet holds more than the ledger explains on two runs in a row, the page warns that the total may be understated.
- **Vanished transactions are flagged.** Recent entries are re-checked on-chain. If one disappears, it is marked for human review and never deleted silently.
- **The page tells you what it covers.** It lists which assets are tracked automatically, which are added by hand, and which have history not fully scanned yet.

You never have to rely on the ledger alone: any transaction can be checked in a public block explorer.

## Use of funds

Contributions are meant for one thing: buying the truck and trailer. The planned path is public wallet, then tractor ($100,000), then trailer ($30,000), then registration and road-ready costs, then a first job. Any income the truck earns afterwards is kept separate from campaign funds.

- **Ownership.** The truck and trailer will be bought and registered to the private individual who runs the experiment and has chosen to stay anonymous. Contributions are voluntary gifts: they give no ownership, equity or returns.
- **Proof.** The actual truck, trailer and documented purchase costs are published in the Purchase Proof section of the site.
- **Conversion.** Paying a seller usually means converting crypto into regular currency. The purchase proof is what documents the real costs.
- **If the goal is not reached.** The goal stays at $130,000 and progress stays visible. Contributions are non-refundable, and any change of plan goes into the change log.
- **Money above $130,000.** It is shown separately as POST-GOAL FUNDS and is intended for children who are orphaned or in unsafe care. Recipients and amounts will be published with receipts.

## Contributor terms

By sending a contribution you agree to the following.

- **It is voluntary.** Nobody has to contribute, and nobody is pressured to.
- **It is non-refundable.** Blockchain transfers cannot be reversed, and the project does not offer refunds.
- **It is not an investment.** No equity, no profit share, no returns, no voting rights, and no token is issued.
- **It is not a charity.** The project is an internet experiment run by a private individual, not a registered non-profit. Do not assume any tax treatment.
- **It buys no perks.** No rewards, memberships or special access are promised.
- **It is not guaranteed to succeed.** The goal may not be reached.
- **It is your own decision.** Contribute only what you can afford to lose, and only where it is lawful for you. Tax and legal rules differ by country and you are responsible for your own. Nothing on the site is legal, tax or financial advice.

## Privacy and anonymity

The person behind the experiment stays anonymous by choice, and the site collects nothing about you.

- **The operator is anonymous.** No name, photo or personal contact detail appears on the website or in this repository. Anonymity is not a reason to trust the project; verification is. That is why the receipts are public.
- **Visitors are not profiled.** The site has no accounts, no forms, no wallet-connect and no advertising cookies. Visits are counted with GoatCounter, a privacy-friendly service that sets no cookies. The QR library ships with the site itself.
- **The blockchain is public.** Your wallet address and the amount you send are visible to everyone on the network. The ledger shows transaction hashes, never names, and the project does not ask for or publish contributor identities.
- **Nothing to hand over.** The project will never ask you for personal documents, a phone number or a login.

## Security and scam protection

Zero to Truck will never ask for anything except a transfer to a public address shown on the official website.

**What the project will never do**

- Ask for a private key, seed phrase or recovery words. Anyone who asks is a scammer.
- Ask you to connect a wallet or sign anything on the site. The site cannot sign or send funds.
- Message you privately to ask for money, "verification" or a different address.
- Promise returns, rewards or to double your money.
- Change a wallet address without recording it in the public change log.
- Let AI move funds. AI writes updates and reports; wallets are controlled by a human, and no keys are on the site.

**How to check you are in the right place**

- Trust only the official website and the X and Instagram accounts linked from it. Copycat sites and accounts are likely.
- Compare the address with the one in this repository (`config.json`), and check the last six characters your wallet shows.
- Send a small test amount first if you are unsure.

**How the site protects its own addresses**

- The QR code is drawn by the page from the address it displays, so the two cannot disagree.
- The QR library is part of this repository with a fixed fingerprint (SHA-256), not loaded from another website.
- Changes to the repository are protected by two-factor authentication and protected branches.

## Known limits and risks

The project would rather tell you where it can fail than have you find out later.

- **Custody is human.** The wallets are controlled by one person, not by an escrow service or a regulated custodian. You are relying on the operator to spend the money as described here.
- **The goal is not guaranteed.** The experiment may stall below $130,000, and contributions are not refundable.
- **Transfers cannot be undone.** Funds sent on the wrong network, to the wrong address, or in an unsupported token are not counted automatically and may not be recoverable.
- **Prices move.** The ledger records the USD value at the moment an entry was recorded. The amount actually received after converting to regular currency can be higher or lower.
- **Some entries are manual.** BNB is added by hand, so it can lag behind the blockchain. Its wallet balance is still watched.
- **Public data sources can fail.** Blockchain and price services can be down or wrong. When that happens the tracker refuses to guess, and the page says its coverage is incomplete. Verified Raised may then be too low, but it is never padded.
- **Rules differ by country.** Crypto and fundraising laws vary, and you are responsible for following those that apply to you.

## FAQ

**Who will own the truck?**
The private individual who runs the experiment and has chosen to stay anonymous. Contributions are gifts and give no ownership, equity or returns.

**Is this a charity or an investment?**
Neither. It is an internet experiment: no equity, no returns.

**What if we do not reach $130,000?**
The goal stays fixed and progress stays visible. Reaching it is not guaranteed, contributions are non-refundable, and any change of plan is published in the change log.

**Where does money above $130,000 go?**
It is shown separately as POST-GOAL FUNDS and is intended for children who are orphaned or in unsafe care. Recipients and amounts will be published with receipts.

**Can the AI move the money?**
No. The AI writes updates and reports. The wallets are controlled by a human, and no keys are on the site.

**How do I verify my payment?**
Use the explorer button next to the address, or paste your transaction hash into any block explorer. Confirmed entries then appear in the public ledger.

**Which network should I use for USDT?**
Pick the one your wallet or exchange supports and select the same one on the site: Ethereum (ERC-20), Tron (TRC-20) or BNB Smart Chain (BEP-20). The wallet card names the network and warns you if you mix them up.

**I sent funds on the wrong network. What now?**
It will not be counted automatically and may not be recoverable. Check the network before every transfer, and send a small test first.

**Why is my contribution not in the ledger yet?**
It appears only after enough confirmations and the next ledger update. The page shows when coverage is incomplete.

## Changes, updates and contact

Important changes are documented, not quietly edited. The website's change log and this repository's history record every change to wallets, the goal and this policy. Official channels are the website and the X and Instagram accounts linked from it; nothing else speaks for the project.

| Date | Change |
| --- | --- |
| 2026-10-26 | Added anonymous, cookie-free visit counts (GoatCounter) |
| 2026-10-23 | Added wallets: USDT (ERC-20, TRC-20, BEP-20), USDC (ERC-20), XRP, TRX |
| 2026-10-07 | Public goal set at $130,000 ($100,000 tractor/truck + $30,000 trailer) |
| 2026-10-07 | Public wallets published: BTC, ETH, SOL, BNB, DOGE |
