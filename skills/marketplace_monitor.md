---
name: marketplace_monitor
description: Monitors Cars and Bids and eBay for car/camera deals
trigger: scheduled
---
Check for new listings matching these targets:
- Nissan S15 Spec R (factory turbo, NOT converted Spec S) under $15,000
- Sony A6700 body under $1,000
- Sony FE 50mm 1.8 lens under $250

Use the marketplace tool. For each new finding:
1. Verify it's actually below market
2. Check for red flags (salvage title, scam pricing, recent flips)
3. Report: title, price, savings vs target, link

Skip listings already seen in previous runs (check memory).

Output should be a concise Discord-friendly summary. Group by target. If
nothing new, say so explicitly — don't pad with filler.
