"""The three lifecycle email templates, and the shell they render inside.

Kept apart from app/services/email.py on purpose: that module owns *whether and when* an
email goes out (claiming, deduping, the provider call), this one owns *what it says*.
Splitting them is what lets the admin console preview a template without any risk of
sending, and lets the copy be edited without touching the send path.

Design notes that are not cosmetic:

  * TABLE-BASED LAYOUT, INLINE STYLES, NO EXTERNAL CSS AND NO IMAGES. Outlook renders
    through Word's HTML engine (no flexbox, no grid), Gmail strips <style> blocks in some
    clients, and a remote image is blocked by default in most of them — so a logo image
    would render as a broken-image box for most recipients on first open. The "pop" border
    is reproduced with a solid border plus a second offset table cell, which is the one
    part of BENTO & POP that survives an email client.

  * EVERY EMAIL SHIPS A PLAIN-TEXT PART. A missing text/plain part is one of the strongest
    spam signals there is, and it matters more here than usual: this is a cold sending
    domain mailing school Google Workspace accounts, which are the least forgiving
    recipients in existence.

  * NO TRACKING PIXEL, no click-wrapped links. Most of this user base are minors and
    legal/privacy.md does not describe open-tracking. If open rates are ever wanted, that
    is a privacy-policy change and a TERMS_VERSION bump first, not a template edit.

  * The unsubscribe link is in the footer of ALL THREE, including the two that are
    defensibly transactional. See email_schema.sql on why the opt-out is honoured for
    every kind rather than for marketing only.
"""
import html

from app.config import EMAIL_APP_URL, EMAIL_POSTAL_ADDRESS


# The three kinds. app/services/email.py validates against this, so an unknown kind is
# refused by name rather than silently producing an empty email.
EMAIL_KINDS = ("welcome", "trial_ending", "goodbye")

# BENTO & POP, reduced to what an email client can actually render (frontend/src/ui/theme.ts).
CREAM = "#FBF8F3"
NAVY = "#1D4E89"
INK = "#1A2540"
INK_SOFT = "#4A6685"
ORANGE = "#F79256"
ORANGE_DEEP = "#F4791D"
HAIRLINE = "#D9DEEB"
MUTED = "#8A93A6"


def _e(value):
    """Escape a value for HTML. Every interpolation below goes through this — a student's
    own first name is untrusted input that we are putting into a document, exactly like any
    other user-supplied string."""
    return html.escape(str(value or ""), quote=True)


def _button(url, label):
    """A bulletproof-ish CTA. A padded <a> rather than a nested table: Outlook drops the
    padding, so the link still works and merely looks plainer there, which is the right way
    round for a degradation."""
    return (
        f'<a href="{_e(url)}" style="display:inline-block;background:{ORANGE};'
        f'color:#FFFFFF;font-family:Helvetica,Arial,sans-serif;font-size:15px;'
        f'font-weight:700;text-decoration:none;padding:14px 26px;border-radius:10px;'
        f'border:2px solid {ORANGE_DEEP}">{_e(label)}</a>'
    )


def _shell(preheader, heading, body_html, unsubscribe_url):
    """The common wrapper: cream canvas, one white card, footer.

    `preheader` is the grey line a client shows next to the subject in the inbox list. It
    is rendered into a hidden div because otherwise the client invents one from the first
    words of the body, which is usually the recipient's own name — the least informative
    text available.
    """
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{_e(heading)}</title>
</head>
<body style="margin:0;padding:0;background:{CREAM}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{_e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CREAM}">
<tr><td align="center" style="padding:32px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px">

    <tr><td style="padding-bottom:20px;font-family:Helvetica,Arial,sans-serif;
        font-size:17px;font-weight:700;color:{NAVY};letter-spacing:-0.2px">
      Highschool Wingman
    </td></tr>

    <tr><td style="background:#FFFFFF;border:2px solid {NAVY};border-radius:14px;padding:32px 28px">
      <h1 style="margin:0 0 16px;font-family:Helvetica,Arial,sans-serif;font-size:24px;
          line-height:32px;font-weight:700;color:{INK}">{_e(heading)}</h1>
      {body_html}
    </td></tr>

    <tr><td style="padding:24px 8px 0;font-family:Helvetica,Arial,sans-serif;
        font-size:12px;line-height:20px;color:{MUTED}">
      You are receiving this because you have a Highschool Wingman account.<br>
      <a href="{_e(unsubscribe_url)}" style="color:{MUTED}">Unsubscribe from these emails</a>
      &nbsp;&middot;&nbsp;
      <a href="{_e(EMAIL_APP_URL)}/privacy.html" style="color:{MUTED}">Privacy</a>
      &nbsp;&middot;&nbsp;
      <a href="{_e(EMAIL_APP_URL)}/terms.html" style="color:{MUTED}">Terms</a>
      <br><br>
      {_e(EMAIL_POSTAL_ADDRESS)}
    </td></tr>

  </table>
</td></tr></table>
</body></html>"""


def _p(text):
    return (f'<p style="margin:0 0 16px;font-family:Helvetica,Arial,sans-serif;font-size:15px;'
            f'line-height:24px;color:{INK_SOFT}">{text}</p>')


def _footer_text(unsubscribe_url):
    return (f"\n\n---\nYou are receiving this because you have a Highschool Wingman account.\n"
            f"Unsubscribe: {unsubscribe_url}\n"
            f"Privacy: {EMAIL_APP_URL}/privacy.html\n"
            f"Terms: {EMAIL_APP_URL}/terms.html\n\n{EMAIL_POSTAL_ADDRESS}\n")


# ---------------- welcome ----------------

def _welcome(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    days = ctx.get("trial_days")
    app = EMAIL_APP_URL

    # The trial length is stated only when it is actually known. A hardcoded "7 days" would
    # be wrong for any account that redeemed a grant code before opening the email, and a
    # wrong number here is the kind of thing a student holds us to.
    trial_line = (f"Your free trial runs for the next {_e(days)} days — "
                  "no card needed until it ends."
                  if days else "Your free trial is running now — no card needed to use it.")

    body = "".join([
        _p(f"Hi {_e(name)} — you're in."),
        _p("Wingman helps you find summer programs, internships, research competitions and "
           "conferences that actually fit you, then keeps track of every deadline so none "
           "of them go past you."),
        _p("Three things worth doing first:"),
        f'<ul style="margin:0 0 20px;padding-left:20px;font-family:Helvetica,Arial,sans-serif;'
        f'font-size:15px;line-height:24px;color:{INK_SOFT}">'
        '<li style="margin-bottom:8px"><strong>My Vibe</strong> — answer a few questions so '
        'the matching has something to work with.</li>'
        '<li style="margin-bottom:8px"><strong>Fresh Finds</strong> — get your first set of '
        'matched opportunities.</li>'
        '<li><strong>Quest Log</strong> — track anything you like the look of, and we watch '
        'its deadlines for you.</li>'
        '</ul>',
        _p(trial_line),
        f'<p style="margin:8px 0 0">{_button(app, "Open Wingman")}</p>',
    ])

    text = (
        f"Hi {name} — you're in.\n\n"
        "Wingman helps you find summer programs, internships, research competitions and\n"
        "conferences that actually fit you, then keeps track of every deadline.\n\n"
        "Three things worth doing first:\n"
        "  1. My Vibe — answer a few questions so the matching has something to work with.\n"
        "  2. Fresh Finds — get your first set of matched opportunities.\n"
        "  3. Quest Log — track anything you like, and we watch its deadlines for you.\n\n"
        + (f"Your free trial runs for the next {days} days — no card needed until it ends.\n\n"
           if days else "Your free trial is running now — no card needed to use it.\n\n")
        + f"Open Wingman: {app}\n"
        + _footer_text(unsubscribe_url)
    )

    return "Welcome to Highschool Wingman", "Here's how to get your first matches", body, text


# ---------------- trial_ending ----------------

def _trial_ending(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    days = ctx.get("days_left")
    ends_on = ctx.get("trial_ends_display")
    app = EMAIL_APP_URL

    # "in 2 days" / "tomorrow" / "today" rather than a bare date. The date is still given
    # underneath, because a relative phrase read three days later is misleading and this is
    # the one email whose whole job is a deadline.
    when = ("today" if days is not None and days <= 0
            else "tomorrow" if days == 1
            else f"in {_e(days)} days" if days is not None
            else "soon")

    body = "".join([
        _p(f"Hi {_e(name)} — your free trial ends <strong>{when}</strong>"
           + (f" ({_e(ends_on)})" if ends_on else "") + "."),
        _p("After that, Wingman is $9.99 a month. Keeping it means your Quest Log, your "
           "profile and every deadline we're watching stay exactly where they are."),
        _p("If you'd rather not continue, you don't need to do anything — nothing is "
           "charged automatically, and the trial simply stops."),
        f'<p style="margin:8px 0 0">{_button(f"{app}/subscription", "Keep my account")}</p>',
    ])

    text = (
        f"Hi {name} — your free trial ends {when}"
        + (f" ({ends_on})" if ends_on else "") + ".\n\n"
        "After that, Wingman is $9.99 a month. Keeping it means your Quest Log, your\n"
        "profile and every deadline we're watching stay exactly where they are.\n\n"
        "If you'd rather not continue, you don't need to do anything — nothing is charged\n"
        "automatically, and the trial simply stops.\n\n"
        f"Keep my account: {app}/subscription\n"
        + _footer_text(unsubscribe_url)
    )

    return f"Your Wingman trial ends {when}", "Nothing is charged automatically", body, text


# ---------------- goodbye ----------------

def _goodbye(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    ends_on = ctx.get("access_ends_display")
    app = EMAIL_APP_URL

    # Deliberately NO win-back discount or promo code. A cancellation confirmation stating
    # the facts is transactional; the same email carrying an offer is commercial, and the
    # exemption this whole feature leans on is not worth trading for a coupon.
    access_line = (f"You keep full access until <strong>{_e(ends_on)}</strong> — you've paid "
                   "for that time and nothing changes before then."
                   if ends_on else
                   "Your access ends at the close of the period you've already paid for.")

    body = "".join([
        _p(f"Hi {_e(name)} — your subscription is cancelled. Sorry to see you go."),
        _p(access_line),
        _p("Your Quest Log and profile aren't deleted. If you come back, everything you "
           "tracked is still there."),
        _p("If something specific pushed you out — a program we never surfaced, a deadline "
           "we got wrong — just reply to this email and tell us. It gets read."),
        f'<p style="margin:8px 0 0">{_button(f"{app}/subscription", "Restart anytime")}</p>',
    ])

    text = (
        f"Hi {name} — your subscription is cancelled. Sorry to see you go.\n\n"
        + (f"You keep full access until {ends_on} — you've paid for that time and nothing\n"
           "changes before then.\n\n" if ends_on else
           "Your access ends at the close of the period you've already paid for.\n\n")
        + "Your Quest Log and profile aren't deleted. If you come back, everything you\n"
          "tracked is still there.\n\n"
          "If something specific pushed you out — a program we never surfaced, a deadline\n"
          "we got wrong — just reply to this email and tell us. It gets read.\n\n"
        f"Restart anytime: {app}/subscription\n"
        + _footer_text(unsubscribe_url)
    )

    return "Your Wingman subscription is cancelled", "Your tracked opportunities are kept", body, text


_BUILDERS = {
    "welcome": _welcome,
    "trial_ending": _trial_ending,
    "goodbye": _goodbye,
}


def render(kind, ctx, unsubscribe_url):
    """Render one email. Returns (subject, html, text).

    `ctx` is a plain dict of already-derived display values, never a raw users row — the
    template must not be the thing that decides how many days are left, or the console
    preview and the real send can disagree about the most important number in the email.
    """
    if kind not in _BUILDERS:
        raise ValueError(f"Unknown email kind: {kind!r}. Known: {', '.join(EMAIL_KINDS)}")
    subject, preheader, body_html, text = _BUILDERS[kind](ctx or {}, unsubscribe_url)
    return subject, _shell(preheader, subject, body_html, unsubscribe_url), text
