# Subscription Feature - Quick Start Guide

## What Was Built

A complete, production-ready subscription system with:
- ✅ **7-day free trial** - All new users automatically get a trial
- ✅ **$9.99/month Pro Plan** - Paid subscription via Stripe
- ✅ **Promo Codes** - Support for promotional codes (1 free month included)
- ✅ **Subscription Management Page** - Users can manage their plan
- ✅ **Trial Countdown** - Visual indicator of days remaining
- ✅ **Stripe Integration** - Secure payment processing

## Getting Started (15 minutes)

### Step 1: Set Up Stripe Account (Free)

1. Go to https://stripe.com and create a free account
2. In Dashboard → Products, create a new product called "Wingman Pro"
3. Set up monthly pricing at $9.99
4. Copy your **Price ID** (looks like `price_...`)

### Step 2: Configure Environment

Add to your `.env` file:
```env
STRIPE_API_KEY=sk_test_YOUR_KEY_HERE
STRIPE_PRICE_ID=price_YOUR_PRICE_ID_HERE
STRIPE_WEBHOOK_SECRET=whsec_YOUR_WEBHOOK_SECRET_HERE
```

(Skip webhook secret for testing - it's optional)

### Step 3: Run Database Migration

In Supabase SQL editor, run:
```sql
-- Don't paste this list by hand — it is incomplete (the consent columns are
-- missing from it). Run subscription_schema.sql instead; see ../SUBSCRIPTION_SETUP.md §1.
```

### Step 4: Test It!

```bash
python server.py
```

1. Sign up a new account
2. Account panel should show "Trial: 7 days left"
3. Click "Manage Plan" to see subscription page
4. Click "Upgrade Now" for Stripe checkout
5. Use test card: `4242 4242 4242 4242`

## API Endpoints

### GET Subscription Status
```bash
POST /api/subscription/status
Content-Type: application/json

{
  "userid": "username"
}
```

Response:
```json
{
  "status": "trial",
  "days_left": 3,
  "trial_ends_at": "2026-08-24T...",
  "is_trial_expired": false
}
```

### Create Checkout
```bash
POST /api/subscription/checkout
Content-Type: application/json

{
  "userid": "username",
  "email": "user@example.com",
  "success_url": "https://...",
  "cancel_url": "https://...",
  "promo_code": "FREEMONTH"  # optional
}
```

Response:
```json
{
  "session_id": "cs_...",
  "checkout_url": "https://checkout.stripe.com/c/pay/cs_...  // whatever Stripe returns; never build this URL yourself"
}
```

### Validate Promo Code
```bash
POST /api/subscription/validate-promo
Content-Type: application/json

{
  "userid": "username",
  "promo_code": "FREEMONTH"
}
```

Response:
```json
{
  "valid": true,
  "description": "1 free month",
  "discount_months": 1
}
```

### Cancel Subscription
```bash
POST /api/subscription/cancel
Content-Type: application/json

{
  "userid": "username"
}
```

## Default Promo Codes

- **FREEMONTH** - Gives 1 free month (extends trial by 30 days)
- **WELCOME10** - 10% off first month

Add more in `subscription_common.py`:
```python
PROMO_CODES = {
    "SUMMER50": {"discount_percent": 50, "description": "50% off summer"},
}
```

## UI Components Added

### Account Panel
- Shows "Trial: X days left" or "Active: $9.99/month"
- "Manage Plan" button links to subscription page

### Subscription Page
- Current plan status with countdown timer
- Free Trial vs Pro Plan comparison
- Promo code input field
- Upgrade button
- Billing information
- Cancel subscription button (for active plans)

## Key Files

| File | Purpose |
|------|---------|
| `subscription_common.py` | Core subscription logic and Stripe API |
| `server.py` | Backend endpoints for subscription |
| `index.html` | UI for subscription management |
| `script.js` | Frontend logic and page rendering |
| `../SUBSCRIPTION_SETUP.md` | Detailed setup and troubleshooting |

## Testing with Stripe

### Test Cards
- **Success:** 4242 4242 4242 4242
- **Failure:** 4000 0000 0000 0002
- **Amex:** 3782 822463 10005

Use any future date for expiry and any 3-digit CVC.

### Test Promo Codes
Users can test promo codes immediately:
1. Enter "FREEMONTH" in promo field
2. Should see "✓ 1 free month"
3. Can be applied at checkout

## Production Checklist

Before going live:

- [ ] Get live Stripe keys (sk_live_...)
- [ ] Update STRIPE_API_KEY with live key
- [ ] Update STRIPE_PRICE_ID with live price
- [ ] Run database migration
- [ ] Enable HTTPS (Stripe requirement)
- [ ] Set up webhook endpoint
- [ ] Test payment with real card
- [ ] Add terms of service
- [ ] Set up email notifications
- [ ] Test cancellation workflow
- [ ] Document refund policy

## Troubleshooting

### "Stripe API key not configured"
Make sure `STRIPE_API_KEY` is set in `.env` and server was restarted.

### Checkout fails with "Invalid Price ID"
Verify `STRIPE_PRICE_ID` matches an actual price in your Stripe account.

### Trial not showing
Try logging out and logging back in. Subscription status is fetched on login.

### Promo code not working
- Check spelling (case-insensitive)
- Verify it exists in `PROMO_CODES` dict in `subscription_common.py`
- User can only use each code once

## Feature Limitations

Current implementation:
- ✓ Trial and paid subscriptions
- ✓ Promo codes
- ✓ Payment processing via Stripe
- ✗ Feature gating by subscription level (not yet)
- ✗ Email notifications (not yet)
- ✗ Automatic webhooks (manual setup needed)
- ✗ Invoice history (not yet)
- ✗ Multiple payment methods (Stripe card only)

## Support

Detailed documentation:
- Setup: `../SUBSCRIPTION_SETUP.md`
- Implementation details: `SUBSCRIPTION_IMPLEMENTATION.md`
- API reference: See ../SUBSCRIPTION_SETUP.md section 6

For issues, check the server logs or browser console for error messages.
