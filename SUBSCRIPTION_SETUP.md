# Subscription Feature Setup Guide

This guide explains how to set up the subscription/payment system for Wingman.

## 1. Database Schema Updates — REQUIRED, and nothing works until it is done

> **Already run** against this project's Supabase on 2026-08-21; all eleven columns are
> live. This section is kept for rebuilding the table, or for standing up a second
> environment.

The migration lives in **[subscription_schema.sql](subscription_schema.sql)**. Open the
Supabase SQL editor, paste the whole file in, run it. That is the only step here.

There is no way to do this from the app: PostgREST — all `server.py` can reach — exposes
REST reads and writes, never DDL. Every statement is `IF NOT EXISTS`, so running it twice
is harmless.

It adds two groups of columns to `users`:

- **Subscription/billing** — `subscription_status`, `trial_ends_at`, `subscription_end_at`,
  `stripe_customer_id`, `stripe_subscription_id`, `promo_codes_used`.
- **Signup consent** — `is_adult`, `parental_consent`, `terms_accepted_at`,
  `privacy_accepted_at`, `terms_version`. See "Consent at signup" below.

**Until you run it, registration is down.** `create_user()` writes every one of those
columns, and Postgres rejects the whole insert if any is missing. `/api/register` detects
that specific failure and returns a 503 naming this file, rather than the bare
`502 Could not reach Supabase` it used to.

### What happens to accounts that already exist

They come out of the migration with `subscription_status = 'trial'` (the column default)
and `trial_ends_at = NULL`. A NULL end date is deliberately read as *"the trial clock has
not started"*, not as *"expired"* — `ensure_trial_started()` in `server.py` stamps a real
3-day window on the row the first time that account signs in. Backfilling the dates in SQL
instead would start everyone's trial the moment you run the migration, burning it for
people who don't come back for a month.

## 2. Stripe Configuration

### 2.1 Create a Stripe Account

1. Sign up for a Stripe account at https://stripe.com
2. Go to the Dashboard and get your API keys
3. You'll need:
   - **Publishable Key** (starts with `pk_`)
   - **Secret Key** (starts with `sk_`) — keep this private!

### 2.2 Create a Stripe Product and Price

In the Stripe Dashboard:

1. Go to **Products** → **+ Add product**
2. Name: "Wingman Pro"
3. Description: "Monthly subscription for Wingman"
4. Pricing:
   - Billing period: Monthly
   - Amount: $9.99
   - Recurring: Yes
5. Copy the **Price ID** (starts with `price_`)

### 2.3 Set Environment Variables

Add these to your `.env` file:

```env
STRIPE_API_KEY=sk_test_... # Your Stripe Secret Key
STRIPE_PRICE_ID=price_... # Your Stripe Price ID
STRIPE_WEBHOOK_SECRET=whsec_... # Webhook secret (see step 2.4)
```

### 2.4 Set Up Webhook (optional but recommended for production)

For production, set up a webhook to handle subscription events:

1. In Stripe Dashboard, go to **Webhooks**
2. Click **+ Add an endpoint**
3. Endpoint URL: `https://yourdomain.com/api/webhook/stripe`
4. Events to send: 
   - `subscription.created`
   - `subscription.updated`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
5. Copy the signing secret and add it to `.env` as `STRIPE_WEBHOOK_SECRET`

## 3. Frontend Configuration

The frontend is already configured to:
- Check subscription status on login
- Show a subscription management page
- Redirect to Stripe checkout when upgrading
- Allow promo code entry before checkout

## 4. Promo Codes

Promo codes live in `PROMO_CODES` in `subscription_common.py`. **There are two kinds, and
they are not interchangeable** — `kind` on each entry decides which path applies it:

| Code | Kind | Effect |
|---|---|---|
| `BETAUSER` | `grant` | Converts a trial into a **beta account with 7 more days** of access |
| `FREEMONTH` | `checkout` | 1 free month, applied as extra trial days at Stripe checkout |
| `WELCOME10` | `checkout` | 10% off the first month (needs a matching Stripe coupon) |

**`grant` codes are redeemed immediately** against the user's own row —
`POST /api/subscription/redeem-promo` sets `subscription_status`, extends
`subscription_end_at`, and appends the code to `promo_codes_used`. No Stripe, no card;
**these work with Stripe entirely unconfigured**, which is what makes `BETAUSER` usable
right now.

**`checkout` codes cannot be applied here at all.** They are discounts that only mean
something once Stripe is in the loop, so they get passed to `create_checkout_session()`.
Redeeming one through the grant endpoint is rejected — it would consume the code and give
nothing back.

`validate-promo` returns `kind` so the UI knows which it is: `applyPromoCode()` redeems a
grant on the spot and says "applied at checkout" for the other kind.

### How `BETAUSER` behaves

- **Additive, not a reset.** The new window is measured from whichever is later, *now* or
  the user's current end date. Someone with 3 days of trial left ends up with 10, not 7 —
  extending from `now` would quietly confiscate the trial they hadn't used yet.
- **Un-expires a lapsed account.** Redeem it from the paywall and the user is let straight
  into the app, no reload — that's the main way this code gets used.
- **One per account**, enforced against `promo_codes_used`.
- **Refused for `active` subscribers.** Applying a 7-day window to someone who is paying
  would be a downgrade, so they're told to save it instead.
- **It ends like a trial does.** When `subscription_end_at` passes, `has_access` goes false
  and the paywall returns, saying *beta access* has ended rather than *trial*.

### Adding another code

```python
PROMO_CODES = {
    # redeemed immediately, no Stripe needed
    "TEACHER": {"kind": "grant", "status": "beta", "grant_days": 30,
                "description": "30 days of beta access"},
    # a discount, applied when the user reaches Stripe checkout
    "SUMMER30": {"kind": "checkout", "discount_percent": 30, "description": "30% off"},
}
```

A `grant` code's `status` must be listed in `GRANTABLE_STATUSES` (currently just `beta`).
That guard exists so a typo can't write a status `subscription_state()` has no branch for —
which would read as *no access* and lock out the user who just redeemed the code.

## 5. Testing

### 5.1 Test in Development

1. Start the server: `python server.py`
2. Sign up a test account
3. Click "Manage Plan" in the account menu
4. Use Stripe test card: `4242 4242 4242 4242`
5. Expiry: any future date
6. CVC: any 3 digits

### 5.2 Test Cards

Stripe provides test card numbers:
- **4242 4242 4242 4242** - Succeeds
- **4000 0000 0000 0002** - Fails
- **3782 822463 10005** - American Express test card

## 6. API Endpoints Reference

The subscription system provides these endpoints:

### GET `/api/subscription/status`
Returns current subscription status:
```json
{
  "status": "trial|active|canceled|past_due",
  "trial_ends_at": "2026-08-24T...",
  "is_trial_expired": false,
  "days_left": 3,
  "subscription_end_at": null,
  "stripe_customer_id": "cus_..."
}
```

### POST `/api/subscription/checkout`
Creates a Stripe checkout session:
```json
{
  "userid": "username",
  "email": "user@example.com",
  "success_url": "https://...",
  "cancel_url": "https://...",
  "promo_code": "FREEMONTH"  // optional
}
```

Returns:
```json
{
  "session_id": "cs_...",
  "checkout_url": "https://checkout.stripe.com/pay/cs_..."
}
```

### POST `/api/subscription/validate-promo`
Validates a promo code:
```json
{
  "userid": "username",
  "promo_code": "FREEMONTH"
}
```

Returns:
```json
{
  "valid": true,
  "kind": "checkout",
  "description": "1 free month",
  "discount_months": 1
}
```

This only *checks* a code — it changes nothing. `kind` tells the caller what to do next:
`"grant"` means redeem it below, `"checkout"` means hold it until the user reaches Stripe.

### POST `/api/subscription/redeem-promo`
Applies a `grant` code to the account. **This one writes.**
```json
{
  "userid": "username",
  "promo_code": "BETAUSER"
}
```

Returns the full refreshed subscription block, so the caller doesn't need a second
status call:
```json
{
  "ok": true,
  "applied": "BETAUSER",
  "description": "Beta access for 1 more week",
  "subscription": {
    "status": "beta",
    "days_left": 7,
    "subscription_end_at": "2026-08-28T22:41:52+00:00",
    "has_access": true
  }
}
```

400s if the code is unknown, already in `promo_codes_used`, a `checkout` code, or if the
account is already `active`.

### POST `/api/subscription/cancel`
Cancels an active subscription:
```json
{
  "userid": "username"
}
```

## 7. Production Checklist

- [ ] Set `STRIPE_API_KEY` (live key, starting with `sk_live_`)
- [ ] Set correct `STRIPE_PRICE_ID` (for live product)
- [ ] Configure webhook endpoint
- [ ] Test payment flow with real cards
- [x] Add terms of service/privacy policy (`legal/*.md` → `terms.html` / `privacy.html`)
- [ ] Review Terms §3, which still says the beta is free of charge — see "Consent at signup"
- [ ] Enable HTTPS (required by Stripe)
- [ ] Set up email receipts
- [ ] Test cancellation flow
- [ ] Document refund policy

## 8. File Structure

- `subscription_common.py` - Stripe API integration and promo code logic
- `server.py` - Backend endpoints for subscription management
- `index.html` - Subscription management page UI
- `script.js` - Frontend subscription logic and page rendering
- `.env` - Stripe API keys (gitignored)
- `subscription_schema.sql` - the one-time DDL from §1
- `legal/terms.md`, `legal/privacy.md` - source of record for the two legal documents
- `terms.html`, `privacy.html` - generated from those by `build_legal.py`; do not hand-edit
- `build_legal.py` - re-run after any edit under `legal/`

## 8b. Consent at signup

Registration collects three acknowledgements before an account is created:

1. **"I am 18 years of age or older."**
2. **"I am at least 13, and my parent or legal guardian has given me permission…"** — shown
   only while box 1 is unticked, and cleared automatically if the user then ticks box 1, so
   nobody ends up submitting both.
3. **"I have read and agree to the Terms of Use and the Privacy Policy"** — links open the
   generated pages in a new tab.

`registerUser()` in `script.js` checks these, and `handle_register()` in `server.py` checks
them again and refuses to create the account otherwise — the browser half is the
explanation, the server half is the control. What was agreed is written onto the user row
(`is_adult`, `parental_consent`, `terms_accepted_at`, `privacy_accepted_at`,
`terms_version`), so you can answer "what did this account actually accept, and when."

`TERMS_VERSION` in `server.py` is the effective date printed at the top of both documents.
**Bump it whenever `legal/*.md` changes materially** — otherwise rows accepted under old
text are indistinguishable from rows accepted under new text.

**Known conflict to resolve before charging anyone:** Terms §3 says "The beta is currently
provided free of charge. We may introduce paid features, subscriptions, or other pricing in
the future. If we do so, we will provide appropriate notice before charging you." That is a
promise the $9.99 plan contradicts as written. Either update §3 (and re-run
`build_legal.py`, and bump `TERMS_VERSION`) or treat the notice requirement as binding
before the first charge.

## 9. Troubleshooting

### "Stripe API key not configured"
Make sure `STRIPE_API_KEY` is set in `.env`

### Checkout session creation fails
- Check that `STRIPE_PRICE_ID` is correct
- Verify the price exists in your Stripe account
- Ensure `STRIPE_API_KEY` is a secret key (not publishable)

### Promo code not working
- Check spelling in `PROMO_CODES` dict
- Verify user hasn't already used the code
- Check that the code exists in `subscription_common.py`

### "Accounts are temporarily unavailable" on registration (503)
The migration in §1 has not been run. Run `subscription_schema.sql` in the Supabase SQL
editor and restart the server.

### Trial expiry: how the gate works
An expired trial with nothing paid blocks the whole app, in two places that both derive
from the single `subscription_state()` helper in `server.py`:

- **Client** — `showApp()` checks `has_access` before the app shell is ever unhidden and
  shows `#page-locked` instead. `checkSubscriptionStatus()` re-checks and swaps to the
  paywall if a trial lapses while a tab is left open.
- **Server** — `Handler._subscription_blocks()` returns **402** from the four endpoints
  that spend real money per call (`/api/messages`, `/api/messages-claude`, the on-demand
  deadline check, and resume extraction). The client-side lock is a screen, not a control;
  this is what stops a lapsed account from billing you. A call with no `userid` is not
  blocked — it cannot be identified at all, the same residual `/api/agents/user-costs`
  reports as unattributed.

Cancelling is **cancel-at-period-end**, so a canceled account keeps access until
`subscription_end_at` passes — they already paid for that time.

## 10. Future Enhancements

- Email notifications for trial ending/renewal
- Subscription history/invoice list
- Multiple subscription tiers
- Annual billing option
- Team/family plans
- Automatic trial extensions for referred users
