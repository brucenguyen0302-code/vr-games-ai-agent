# Scenario: Sales pricing lookup

**Root agent under test:** `sales_agent`
**Generated:** 2026-07-21T16:23:48+10:00

A straightforward pricing question. sales_agent should ground the answer in `get_pricing` rather than inventing a number.

## Conversation

**User:**
> how much is the 360 flight for 2 people?

**Trace:**

1. **`sales_agent`** calls tool `get_pricing`
   ```json
   {
     "attraction_name": "360 flight"
   }
   ```
2. **`sales_agent`** ← result from `get_pricing`
   ```json
   {
     "id": 4,
     "attraction_id": 4,
     "attraction_name": "360 Flight",
     "category": "vr_ride",
     "duration_minutes": null,
     "price_aud": 25.0,
     "per_person": true
   }
   ```
3. **`sales_agent`** **final response**:
   > The 360 Flight is $25 per person, so for 2 people, it would be $50 in total. Would you like me to check availability for a session?

## Final Response

> The 360 Flight is $25 per person, so for 2 people, it would be $50 in total. Would you like me to check availability for a session?

## Claim Guard

No unverified action claims detected — the guard did not need to intervene.

## Assertions

- **PASS** — sales_agent called `get_pricing` instead of inventing a price
- **PASS** — final response quotes the real per-person price ($25)

## Result: PASS
