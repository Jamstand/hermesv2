---
name: credit_tracker
description: Tracks credit-building progress toward S15 financing
trigger: manual
---
Help Josh track his credit-building journey for S15 import financing.

Current plan:
- Discover it Secured card ($500 deposit)
- Authorized user on family member's old account
- Self credit builder loan
- Experian Boost (utilities, subscriptions)
- Target: 700+ FICO for LightStream loan approval
- Timeline: 6-12 months from start

When asked, track:
- Current credit score
- Months since starting
- Cards/loans open
- Utilization ratio (keep under 30%)
- Payment history (must be 100% on-time)
- Estimated timeline to 700+

Remind about:
- Never miss a payment (autopay everything)
- Don't apply for multiple cards at once
- Don't max out the card
- Pay in full each month

Pull stored facts from memory (keys: `credit_score`, `credit_start_date`,
`credit_card_*`, `credit_loan_*`) and update them when Josh provides
new numbers.
