"""The three lifecycle email templates, and the shell they render inside.

Kept apart from app/services/email.py on purpose: that module owns *whether and when* an
email goes out (claiming, deduping, the provider call), this one owns *what it says*.
Splitting them is what lets the admin console preview a template without any risk of
sending, and lets the copy be edited without touching the send path.

DESIGN PROVENANCE. The layout here is a port of three hand-designed HTML files
(wingman-trial-welcome / -ending / -ended-goodbye): BENTO & POP as an email — cream canvas,
a white hero card with a 3px navy border and the hard offset "pop" shadow, soft feature
cards under it, and a dark gradient CTA banner. Four things had to change on the way in,
and each is a correctness issue rather than a matter of taste:

  1. THE LOGO GLYPH IS REBUILT AS A TABLE. The source drew it with six
     `position:absolute` divs. Gmail strips `position` outright and Outlook renders through
     Word's HTML engine, so all six would collapse to the same origin — four orange bars
     and two yellow dots stacked on one spot, which reads as a broken image rather than as
     a logo. It is now nested tables with `bgcolor` and fixed cell heights, which renders
     the same shape everywhere. `border-radius` degrades to square corners in Outlook and
     nothing else moves.

  2. EVERY VALUE IS INTERPOLATED, NOT HARDCODED. The source carried `{{first_name}}` plus
     a literal "7 days" and "tomorrow" in the badge, the preheader, the heading and the
     dark banner. A trial can be extended by a `grant` promo code, so "7" is wrong for any
     account that redeemed one, and "tomorrow" is wrong for four of the five days the
     reminder window can fire on. All of it now comes from `ctx`, which
     app/services/email.py derives in ONE place so a preview and a real send cannot
     disagree about the most important number in the message.

  3. THE UNSUBSCRIBE LINK IS A REAL UNSUBSCRIBE. The source pointed it at /login, which
     signs a student in rather than opting them out — the exact silent failure this whole
     feature is measured against. It now carries the HMAC token
     app/routes/email.py verifies.

  4. NO IMAGES, AND THE POSTAL ADDRESS IS REAL. `[Add your company name and postal address
     here]` is replaced by EMAIL_POSTAL_ADDRESS. Remote images are blocked by default in
     most clients, so nothing here loads an external asset.

KNOWN DEGRADATION, ACCEPTED. The feature-card icons are inline `<svg>`, which Gmail and
Outlook.com strip. The 40px icon column then renders empty and the card's text is
unaffected — the layout holds, so this is a cosmetic loss in some clients rather than a
broken email. Left as SVG deliberately: the alternative is an emoji, which renders
inconsistently and reads as less considered than a blank column.

OTHER RULES THAT STILL APPLY:

  * TABLE-BASED LAYOUT, INLINE STYLES. Outlook has no flexbox and no grid.
  * EVERY EMAIL SHIPS A PLAIN-TEXT PART. The source files had none. A missing text/plain
    part is among the strongest spam signals there is, and it matters more than usual
    here: a cold sending domain mailing school Google Workspace accounts.
  * NO TRACKING PIXEL, no click-wrapped links. Most of this user base are minors and
    legal/privacy.md does not describe open-tracking. Adding it is a privacy-policy change
    and a TERMS_VERSION bump, not a template edit.
  * The unsubscribe link is in the footer of ALL THREE, including the two that are
    defensibly transactional. See db/email_schema.sql.
"""
import html

from app.config import EMAIL_APP_URL, EMAIL_POSTAL_ADDRESS


# The lifecycle kinds. app/services/email.py validates against this, so an unknown kind is
# refused by name rather than silently producing an empty email. `deadline_alert` is a
# DIGEST (a list of a student's own tracked deadlines), unlike the other three, which are
# single-fact account-lifecycle notices — see _deadline_alert below.
EMAIL_KINDS = ("welcome", "trial_ending", "goodbye", "deadline_alert")

# Palette, from the source files (which track frontend/src/ui/theme.ts).
CREAM = "#FBF8F3"
NAVY = "#1d4e89"
ORANGE = "#F97316"
ORANGE_SOFT = "#FFF3E9"
YELLOW = "#FACC15"
BODY = "#374151"
MUTED = "#8a8579"
BANNER_DARK = "#101c36"
BANNER_LIGHT = "#182750"
BANNER_TEXT = "#c7d0e0"
CARD_GREY = "#EEF2F8"

DISPLAY = "'Trebuchet MS', Verdana, sans-serif"
SANS = "Arial, Helvetica, sans-serif"


def _e(value):
    """Escape a value for HTML. Every interpolation below goes through this — a student's
    own first name is untrusted input being put into a document, exactly like any other
    user-supplied string."""
    return html.escape(str(value or ""), quote=True)


# ---------------- Structural pieces ----------------

def _logo():
    """Wordmark plus the bar-chart glyph, rebuilt as nested tables (see note 1 above).

    The bars are bottom-aligned cells of increasing height; the dot sits in a row above the
    tallest one. `font-size:1px;line-height:1px` on each spacer cell is what stops Outlook
    imposing a minimum line height and inflating the whole glyph.
    """
    def bar(height):
        return (f'<td width="6" valign="bottom"><table role="presentation" cellpadding="0" '
                f'cellspacing="0" border="0"><tr><td width="6" height="{height}" '
                f'bgcolor="{ORANGE}" style="width:6px;height:{height}px;background-color:{ORANGE};'
                f'border-radius:2px;font-size:1px;line-height:1px">&nbsp;</td></tr></table></td>')

    gap = '<td width="4" style="width:4px;font-size:1px;line-height:1px">&nbsp;</td>'
    dot = (f'<td width="6" align="center" style="font-size:1px;line-height:1px">'
           f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
           f'<tr><td width="8" height="8" bgcolor="{YELLOW}" style="width:8px;height:8px;'
           f'background-color:{YELLOW};border-radius:50%;font-size:1px;line-height:1px">'
           f'&nbsp;</td></tr></table></td>')
    blank = '<td width="6" style="font-size:1px;line-height:1px">&nbsp;</td>'

    return f"""
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td valign="bottom" style="padding-right:9px">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>{blank}{gap}{blank}{gap}{blank}{gap}{dot}</tr>
              <tr><td colspan="7" height="3" style="height:3px;font-size:1px;line-height:1px">&nbsp;</td></tr>
              <tr>{bar(11)}{gap}{bar(16)}{gap}{bar(21)}{gap}{bar(26)}</tr>
            </table>
          </td>
          <td valign="middle">
            <span style="font-family:{DISPLAY};font-weight:700;font-size:22px;color:{NAVY};
                  letter-spacing:-0.3px">Wingman</span>
          </td>
        </tr>
      </table>"""


def _cta(url, label, width_px):
    """The primary button, with a VML fallback so Outlook gets the pill shape and the
    border instead of a bare blue link."""
    return f"""
      <!--[if mso]>
      <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{_e(url)}"
        style="height:52px;v-text-anchor:middle;width:{width_px}px;" arcsize="50%"
        strokecolor="{NAVY}" strokeweight="3px" fillcolor="{ORANGE}">
      <w:anchorlock/>
      <center style="color:{NAVY};font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">{_e(label)}</center>
      </v:roundrect>
      <![endif]-->
      <!--[if !mso]><!-->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center" bgcolor="{ORANGE}" style="border-radius:999px;border:3px solid {NAVY};
              box-shadow:3px 3px 0 {NAVY}">
            <a href="{_e(url)}" target="_blank" style="display:block;padding:14px 32px;
               font-family:{SANS};font-size:16px;font-weight:700;color:{NAVY};
               text-decoration:none">{_e(label)}</a>
          </td>
        </tr>
      </table>
      <!--<![endif]-->"""


def _hero(badge, badge_fg, badge_bg, heading, body, cta_url, cta_label, cta_width,
          subnote=None):
    sub = ""
    if subnote:
        sub = (f'<tr><td align="center" style="padding-top:16px;font-family:{SANS};'
               f'font-size:13px;color:{MUTED}">{subnote}</td></tr>')
    return f"""
  <tr>
    <td style="background-color:#FFFFFF;border:3px solid {NAVY};border-radius:22px;
        box-shadow:6px 6px 0 {NAVY}">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td class="pad-mobile" style="padding:40px 40px 36px 40px" align="center">
            <span style="display:inline-block;font-family:{SANS};font-size:12px;font-weight:700;
                  letter-spacing:1px;text-transform:uppercase;color:{badge_fg};
                  background-color:{badge_bg};border-radius:999px;padding:6px 14px">{badge}</span>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td align="center" style="padding:20px 0 14px 0;font-family:{DISPLAY};
                    font-weight:800;font-size:30px;line-height:1.25;color:{NAVY}">{heading}</td>
              </tr>
              <tr>
                <td align="center" style="padding:0 10px 26px 10px;font-family:{SANS};
                    font-size:16px;line-height:1.6;color:{BODY}">{body}</td>
              </tr>
              <tr><td align="center">{_cta(cta_url, cta_label, cta_width)}</td></tr>
              {sub}
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>"""


def _section(title):
    return f"""
  <tr>
    <td align="left" class="pad-mobile" style="padding:0 4px 16px 4px;font-family:{DISPLAY};
        font-weight:800;font-size:18px;color:{NAVY}">{title}</td>
  </tr>"""


def _cards(items):
    """Soft feature cards. `items` is [(icon_svg, title, body)]."""
    rows = []
    for i, (icon, title, body) in enumerate(items):
        if i:
            rows.append('<tr><td style="height:14px;font-size:1px;line-height:14px">&nbsp;</td></tr>')
        rows.append(f"""
        <tr>
          <td style="background-color:#FFFFFF;border-radius:22px;
              box-shadow:0 2px 18px rgba(15,23,42,0.06);padding:22px 24px">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="40" valign="top">{icon}</td>
                <td style="padding-left:14px;font-family:{SANS}">
                  <div style="font-family:{DISPLAY};font-weight:700;font-size:16px;color:{NAVY};
                       padding-bottom:4px">{title}</div>
                  <div style="font-size:14px;line-height:1.55;color:{BODY}">{body}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>""")
    return ('\n  <tr><td>\n    <table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0">' + "".join(rows) + "</table>\n  </td></tr>")


def _banner(title, body, cta_url, cta_label):
    """Dark CTA banner. background-color is set as well as the gradient — Outlook ignores
    linear-gradient entirely and would otherwise render white text on white."""
    return f"""
  <tr>
    <td class="pad-mobile" style="background-color:{BANNER_DARK};
        background-image:linear-gradient(90deg,{BANNER_DARK},{BANNER_LIGHT});
        border-radius:22px;padding:28px 32px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center" style="font-family:{DISPLAY};font-weight:700;font-size:17px;
              color:#FFFFFF;padding-bottom:8px">{title}</td>
        </tr>
        <tr>
          <td align="center" style="font-family:{SANS};font-size:14px;line-height:1.6;
              color:{BANNER_TEXT};padding-bottom:18px">{body}</td>
        </tr>
        <tr>
          <td align="center">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td align="center" bgcolor="{ORANGE}" style="border-radius:999px">
                  <a href="{_e(cta_url)}" target="_blank" style="display:block;padding:12px 28px;
                     font-family:{SANS};font-size:14px;font-weight:700;color:{NAVY};
                     text-decoration:none">{_e(cta_label)}</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>"""


def _gap(height):
    return (f'\n  <tr><td style="height:{height}px;line-height:{height}px;font-size:1px">'
            '&nbsp;</td></tr>')


def _footer(reason, unsubscribe_url):
    return f"""
  <tr>
    <td align="center" style="font-family:{SANS};font-size:12px;line-height:1.7;
        color:{MUTED};padding:0 20px">
      This app is in beta &mdash; features are actively evolving and results may
      occasionally be incomplete or inaccurate.
      <br><br>
      {reason}
      <a href="{_e(unsubscribe_url)}" style="color:{NAVY};text-decoration:underline">Unsubscribe</a>
      &nbsp;&middot;&nbsp;
      <a href="{_e(EMAIL_APP_URL)}/privacy.html" style="color:{NAVY};text-decoration:underline">Privacy</a>
      &nbsp;&middot;&nbsp;
      <a href="{_e(EMAIL_APP_URL)}/terms.html" style="color:{NAVY};text-decoration:underline">Terms</a>
      <br><br>
      {_e(EMAIL_POSTAL_ADDRESS)}
    </td>
  </tr>"""


def _shell(title, preheader, content, reason, unsubscribe_url):
    """The document. `preheader` is the grey line a client shows beside the subject in the
    inbox list; it is hidden in the body because otherwise the client invents one from the
    first words of the message, which here is the recipient's own name — the least
    informative text available. The zero-width joiners after it stop Gmail pulling the
    following visible text in behind it."""
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(title)}</title>
<!--[if mso]>
<noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
<![endif]-->
<style>
  body, table, td {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ -ms-interpolation-mode: bicubic; border: 0; }}
  body {{ margin: 0; padding: 0; width: 100% !important; background-color: {CREAM}; }}
  a {{ text-decoration: none; }}
  @media only screen and (max-width: 620px) {{
    .email-container {{ width: 100% !important; }}
    .pad-mobile {{ padding-left: 20px !important; padding-right: 20px !important; }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background-color:{CREAM};">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all; font-size:1px; line-height:1px; color:{CREAM};">
  {preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CREAM};">
<tr>
<td align="center" style="padding:32px 16px;">

<table role="presentation" class="email-container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px; max-width:600px;">

  <tr><td align="center" style="padding:0 0 28px 0;">{_logo()}</td></tr>
{content}
{_gap(36)}
{_footer(reason, unsubscribe_url)}

</table>
</td>
</tr>
</table>
</body>
</html>"""


# ---------------- Icons (see the KNOWN DEGRADATION note) ----------------

_ICON_SEARCH = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
    f'<circle cx="11" cy="11" r="7" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<line x1="21" y1="21" x2="16.65" y2="16.65" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>")

_ICON_CALENDAR = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
    f'<rect x="3" y="5" width="18" height="16" rx="2" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<line x1="16" y1="3" x2="16" y2="7" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<line x1="8" y1="3" x2="8" y2="7" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<line x1="3" y1="10" x2="21" y2="10" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>")

_ICON_PROFILE = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
    f'<circle cx="12" cy="8" r="4" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<path d="M4 20c0-4 4-6 8-6s8 2 8 6" stroke="{NAVY}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>")


def _footer_text(reason, unsubscribe_url):
    return (
        "\n\n---\n"
        "This app is in beta — features are actively evolving and results may occasionally\n"
        "be incomplete or inaccurate.\n\n"
        f"{reason}\n"
        f"Unsubscribe: {unsubscribe_url}\n"
        f"Privacy: {EMAIL_APP_URL}/privacy.html\n"
        f"Terms: {EMAIL_APP_URL}/terms.html\n\n"
        f"{EMAIL_POSTAL_ADDRESS}\n")


_WELCOME_REASON = "You&rsquo;re receiving this because you started a Wingman trial."
_WELCOME_REASON_TXT = "You're receiving this because you started a Wingman trial."
_GOODBYE_REASON = "You&rsquo;re receiving this because you had a Wingman subscription."
_GOODBYE_REASON_TXT = "You're receiving this because you had a Wingman subscription."


# ---------------- welcome ----------------

def _welcome(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    days = ctx.get("trial_days")
    app = EMAIL_APP_URL

    # "7" is never hardcoded: a grant promo code extends the trial, so the number is only
    # true for an account that redeemed nothing. With no known figure the copy drops the
    # count rather than guessing one.
    badge = f"{_e(days)}-day trial started" if days else "Trial started"
    span = f"the next {_e(days)} days" if days else "your trial period"
    banner_title = (f"Your trial ends in {_e(days)} days" if days
                    else "Your trial is running now")

    content = "".join([
        _hero(
            badge=badge, badge_fg=ORANGE, badge_bg=ORANGE_SOFT,
            heading=f"You&rsquo;re in, {_e(name)} &#127881;",
            body=(f"Your Wingman trial is live for {span}. That&rsquo;s enough time to build "
                  "your profile, get matched to real opportunities, and start tracking "
                  "deadlines &mdash; no credit card, no pressure."),
            cta_url=app, cta_label="Start exploring →", cta_width=240),
        _gap(32),
        _section("Here&rsquo;s what&rsquo;s waiting for you"),
        _cards([
            (_ICON_SEARCH, "Fresh Finds",
             "Summer programs, internships, and competitions matched to your interests, "
             "grade, and budget &mdash; out of a catalog of 1,300+."),
            (_ICON_CALENDAR, "Quest Log",
             "Track every deadline you&rsquo;re chasing on one calendar, so nothing slips "
             "through the cracks."),
            (_ICON_PROFILE, "My Vibe",
             "Not sure what you&rsquo;re looking for? A quick quiz builds your profile so we "
             "can match you to the right things."),
        ]),
        _gap(32),
        _banner(banner_title,
                "Build your profile now so your matches are ready before the trial&rsquo;s up.",
                f"{app}/profile", "Build my profile"),
    ])

    text = (
        f"You're in, {name}\n\n"
        f"Your Wingman trial is live for {'the next %s days' % days if days else 'your trial period'}.\n"
        "That's enough time to build your profile, get matched to real opportunities, and\n"
        "start tracking deadlines — no credit card, no pressure.\n\n"
        f"Start exploring: {app}\n\n"
        "HERE'S WHAT'S WAITING FOR YOU\n\n"
        "  Fresh Finds — summer programs, internships and competitions matched to your\n"
        "    interests, grade and budget, out of a catalog of 1,300+.\n"
        "  Quest Log — track every deadline you're chasing on one calendar.\n"
        "  My Vibe — a quick quiz builds your profile so we can match you properly.\n\n"
        + (f"Your trial ends in {days} days. " if days else "")
        + "Build your profile now so your matches are ready before the trial's up.\n"
        f"{app}/profile\n"
        + _footer_text(_WELCOME_REASON_TXT, unsubscribe_url)
    )

    return ("Welcome to your Wingman trial",
            "Your Wingman trial just started &mdash; let&rsquo;s find you something worth applying to.",
            content, text, _WELCOME_REASON, unsubscribe_url)


# ---------------- trial_ending ----------------

def _trial_ending(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    days = ctx.get("days_left")
    ends_on = ctx.get("trial_ends_display")
    app = EMAIL_APP_URL

    # The source hardcoded "tomorrow" in the badge, the preheader and the heading. The
    # reminder window can fire on any of several days, so it is computed once here and
    # reused, and the absolute date is given underneath — a relative phrase read three days
    # later is misleading, and this is the one email whose whole job is a deadline.
    when = ("today" if days is not None and days <= 0
            else "tomorrow" if days == 1
            else f"in {_e(days)} days" if days is not None
            else "soon")
    badge = ("Trial ends today" if when == "today"
             else "Trial ends tomorrow" if when == "tomorrow"
             else f"Trial ends {when}")
    dated = f" ({_e(ends_on)})" if ends_on else ""

    content = "".join([
        _hero(
            badge=badge, badge_fg=ORANGE, badge_bg=ORANGE_SOFT,
            heading=f"Your trial wraps up {when}, {_e(name)}",
            body=("Keep your matches, your tracked deadlines, and everything you&rsquo;ve "
                  f"built in Wingman by subscribing before your trial ends{dated}."),
            cta_url=f"{app}/subscription", cta_label="Keep my plan →", cta_width=260,
            subnote="$9.99/month after your trial &mdash; cancel anytime."),
        _gap(32),
        _section("Don&rsquo;t lose what you&rsquo;ve built"),
        _cards([
            (_ICON_SEARCH, "Fresh Finds",
             "Your matches stop updating once the trial ends. Subscribe to keep discovering "
             "new opportunities."),
            (_ICON_CALENDAR, "Quest Log",
             "Every deadline you&rsquo;re tracking stays put &mdash; but reminders pause if "
             "your trial lapses."),
        ]),
        _gap(32),
        _banner("Less than a Netflix subscription",
                "$9.99/month keeps your matches, deadlines, and profile all in one place.",
                f"{app}/subscription", "Subscribe now"),
    ])

    text = (
        f"Your trial wraps up {when}, {name}"
        + (f" ({ends_on})" if ends_on else "") + ".\n\n"
        "Keep your matches, your tracked deadlines, and everything you've built in Wingman\n"
        "by subscribing before your trial ends. $9.99/month — cancel anytime.\n\n"
        f"Keep my plan: {app}/subscription\n\n"
        "DON'T LOSE WHAT YOU'VE BUILT\n\n"
        "  Fresh Finds — your matches stop updating once the trial ends.\n"
        "  Quest Log — your tracked deadlines stay put, but reminders pause.\n\n"
        "Nothing is charged automatically. If you'd rather not continue, you don't need to\n"
        "do anything.\n"
        + _footer_text(_WELCOME_REASON_TXT, unsubscribe_url)
    )

    return (f"Your Wingman trial ends {when}",
            f"Your Wingman trial ends {when} &mdash; keep your matches and deadlines by subscribing.",
            content, text, _WELCOME_REASON, unsubscribe_url)


# ---------------- goodbye ----------------

def _goodbye(ctx, unsubscribe_url):
    name = ctx.get("first_name") or "there"
    ends_on = ctx.get("access_ends_display")
    app = EMAIL_APP_URL

    # This fires on CANCELLATION, so the copy says cancelled rather than "your trial has
    # ended" as the source file did — see the note in CLAUDE.md about the two being
    # different events with different triggers. Cancelling is cancel-at-period-end
    # (subscription_common.cancel_subscription), so the access date is the sentence that
    # matters most and is stated first.
    access = (f"You keep full access until <strong>{_e(ends_on)}</strong> &mdash; you&rsquo;ve "
              "paid for that time and nothing changes before then. "
              if ends_on else
              "Your access runs to the end of the period you&rsquo;ve already paid for. ")

    content = "".join([
        _hero(
            badge="Subscription cancelled", badge_fg=NAVY, badge_bg=CARD_GREY,
            heading=f"Sorry to see you go, {_e(name)}",
            body=(access + "Your profile, saved matches, and tracked deadlines are still "
                  "here whenever you&rsquo;re ready to come back."),
            cta_url=f"{app}/subscription", cta_label="Come back →", cta_width=220,
            subnote="$9.99/month, cancel anytime."),
        _gap(32),
        # The feedback card, kept from the source. A reply actually reaches somebody:
        # EMAIL_REPLY_TO defaults to the same address this mailto uses.
        f"""
  <tr>
    <td class="pad-mobile" style="background-color:#FFFFFF;border-radius:22px;
        box-shadow:0 2px 18px rgba(15,23,42,0.06);padding:26px 28px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="left" style="font-family:{DISPLAY};font-weight:700;font-size:16px;
              color:{NAVY};padding-bottom:8px">Got a minute?</td>
        </tr>
        <tr>
          <td align="left" style="font-family:{SANS};font-size:14px;line-height:1.6;
              color:{BODY};padding-bottom:16px">
            We&rsquo;re a small team building this for students and families. If there&rsquo;s
            something that would&rsquo;ve made Wingman worth keeping, we&rsquo;d love to hear it.
          </td>
        </tr>
        <tr>
          <td align="left">
            <a href="mailto:contactus@highschoolwingman.com" target="_blank"
               style="font-family:{SANS};font-size:14px;font-weight:700;color:{NAVY};
               text-decoration:underline">Share feedback &rarr;</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>""",
    ])

    text = (
        f"Sorry to see you go, {name}.\n\n"
        + (f"Your subscription is cancelled. You keep full access until {ends_on} — you've\n"
           "paid for that time and nothing changes before then.\n\n" if ends_on else
           "Your subscription is cancelled. Your access runs to the end of the period you've\n"
           "already paid for.\n\n")
        + "Your profile, saved matches, and tracked deadlines are still here whenever you're\n"
          "ready to come back.\n\n"
        f"Come back: {app}/subscription  ($9.99/month, cancel anytime)\n\n"
        "GOT A MINUTE?\n"
        "We're a small team building this for students and families. If there's something\n"
        "that would've made Wingman worth keeping, we'd love to hear it — just reply, or\n"
        "write to contactus@highschoolwingman.com.\n"
        + _footer_text(_GOODBYE_REASON_TXT, unsubscribe_url)
    )

    return ("Sorry to see you go",
            "Your Wingman subscription is cancelled &mdash; your profile will be waiting.",
            content, text, _GOODBYE_REASON, unsubscribe_url)


# ---------------- deadline_alert ----------------
#
# The one DIGEST kind. Where the other three carry a single computed fact, this carries a
# list of the student's own tracked deadlines, grouped by how soon each is. Two rules make it
# safe: an EMPTY digest is refused (never send "here are your 0 deadlines"), and every value
# comes through `ctx` — app/services/email.py runs the reader + rung engine and hands this a
# plain, already-sorted list, so the console preview and the real send cannot disagree about
# the dates. An estimated (or unknown-provenance) date is always labelled; only a date the
# blob explicitly marks estimated:false is presented bare.

_DEADLINE_REASON = ("You&rsquo;re receiving this because you&rsquo;re tracking these "
                    "deadlines in Wingman.")
_DEADLINE_REASON_TXT = ("You're receiving this because you're tracking these deadlines in "
                        "Wingman.")

# Rung -> the section heading that rung's items sit under. Keys are the DEADLINE_ALERT_RUNGS
# values; a rung with no items is simply not rendered.
_RUNG_TITLE = {
    1: "Due today or tomorrow",
    3: "Due in the next few days",
    7: "Due this week",
}

_EST_HTML = " &middot; <em>estimated &mdash; confirm on the program&rsquo;s site</em>"
_EST_TXT = " (estimated — confirm on the program's site)"


def _relative_days(days):
    if days is None:
        return "soon"
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _deadline_card_title(alert):
    """The program name, linked to its opportunity URL when there is one. The link is on the
    NAME (not a separate 'view' link) so a student taps the thing they recognise. Falls back
    to plain text when the tracked item carries no usable URL."""
    name = _e(alert.get("name") or "This opportunity")
    url = alert.get("url")
    if url:
        return (f'<a href="{_e(url)}" target="_blank" '
                f'style="color:{NAVY};text-decoration:underline">{name}</a>')
    return name


def _deadline_card_body(alert):
    """The body of one deadline card: org (if known), then 'Label: <date>' with the estimated
    note where it applies."""
    lines = []
    org = alert.get("org")
    if org:
        lines.append(_e(org))
    label = alert.get("label") or "Deadline"
    date_display = alert.get("date_display") or alert.get("date_iso") or ""
    row = f"{_e(label)}: <strong>{_e(date_display)}</strong>"
    # is not False -> True or None (unknown). Unknown is labelled estimated, never confirmed.
    if alert.get("estimated") is not False:
        row += _EST_HTML
    lines.append(row)
    return "<br>".join(lines)


def _overflow_row(count, url):
    link = (f'<a href="{_e(url)}" style="color:{NAVY};text-decoration:underline">'
            f'{count} more in your Quest Log</a>')
    return f"""
  <tr>
    <td align="center" class="pad-mobile" style="padding:2px 4px 0 4px;font-family:{SANS};
        font-size:14px;color:{MUTED}">&hellip; and {link}.</td>
  </tr>"""


def _deadline_alert_text(name, alerts, shown, groups, overflow, app, unsubscribe_url):
    total = len(alerts)
    lines = []
    if total == 1:
        lines.append(f"One deadline is {_relative_days(alerts[0]['days_left'])}, {name}.")
    else:
        lines.append(f"{total} deadlines coming up, {name} — the first is "
                     f"{_relative_days(alerts[0]['days_left'])}.")
    lines.append("")
    for rung in sorted(_RUNG_TITLE):
        items = groups.get(rung)
        if not items:
            continue
        lines.append(_RUNG_TITLE[rung].upper())
        for a in items:
            org = f" ({a['org']})" if a.get("org") else ""
            est = _EST_TXT if a.get("estimated") is not False else ""
            date_display = a.get("date_display") or a.get("date_iso") or ""
            lines.append(f"  - {a.get('name') or 'This opportunity'}{org} — "
                         f"{a.get('label') or 'Deadline'}: {date_display}{est}")
            if a.get("url"):
                lines.append(f"    {a['url']}")
        lines.append("")
    if overflow > 0:
        lines.append(f"...and {overflow} more in your Quest Log.")
        lines.append("")
    lines.append(f"Open your Quest Log: {app}/tracker")
    return "\n".join(lines) + _footer_text(_DEADLINE_REASON_TXT, unsubscribe_url)


def _deadline_alert(ctx, unsubscribe_url):
    from app.config import DEADLINE_ALERT_MAX_ITEMS

    name = ctx.get("first_name") or "there"
    alerts = list(ctx.get("alerts") or [])
    # An empty digest is refused, not sent. "Here are your 0 deadlines" is the one message
    # this feature must never produce; the sweep also guards against it, so this is the
    # second line of defence and the one that protects the preview/test path too.
    if not alerts:
        raise ValueError("deadline_alert has no due dates; an empty digest must not be sent")

    app = EMAIL_APP_URL
    total = len(alerts)
    shown = alerts[:DEADLINE_ALERT_MAX_ITEMS]
    overflow = total - len(shown)
    soonest = alerts[0].get("days_left")
    rel = _relative_days(soonest)

    if total == 1:
        only = alerts[0].get("name") or "your tracked opportunity"
        subject = f"Deadline {rel}: {only}"
        preheader = f"Your {_e(only)} deadline is {rel}."
        badge = f"Deadline {rel}"
        heading = f"One deadline is {rel}, {_e(name)}"
        intro = ("You&rsquo;re tracking one deadline that&rsquo;s coming up &mdash; "
                 "here&rsquo;s where it stands.")
    else:
        subject = f"{total} deadlines coming up — first {rel}"
        preheader = f"{total} deadlines you&rsquo;re tracking are coming up soon."
        badge = f"{total} deadlines coming up"
        heading = f"{total} deadlines coming up, {_e(name)}"
        intro = (f"You&rsquo;re tracking {total} deadlines closing soon &mdash; the first is "
                 f"{rel}. Here&rsquo;s where things stand.")

    # Group the shown items by rung, rendered in ladder order (1, 3, 7).
    groups = {}
    for a in shown:
        groups.setdefault(a.get("rung"), []).append(a)

    pieces = [
        _hero(badge=badge, badge_fg=ORANGE, badge_bg=ORANGE_SOFT,
              heading=heading, body=intro,
              cta_url=f"{app}/tracker", cta_label="Open my Quest Log →", cta_width=260),
        _gap(30),
    ]
    for rung in sorted(_RUNG_TITLE):
        items = groups.get(rung)
        if not items:
            continue
        pieces.append(_section(_RUNG_TITLE[rung]))
        pieces.append(_cards([
            (_ICON_CALENDAR, _deadline_card_title(a), _deadline_card_body(a))
            for a in items
        ]))
        pieces.append(_gap(22))
    if overflow > 0:
        pieces.append(_overflow_row(overflow, f"{app}/tracker"))
        pieces.append(_gap(10))
    pieces.append(_banner(
        "Don&rsquo;t let one slip",
        "Open Wingman to see the full details, requirements, and check off your progress.",
        f"{app}/tracker", "Go to my deadlines"))

    content = "".join(pieces)
    text = _deadline_alert_text(name, alerts, shown, groups, overflow, app, unsubscribe_url)
    return subject, preheader, content, text, _DEADLINE_REASON, unsubscribe_url


_BUILDERS = {
    "welcome": _welcome,
    "trial_ending": _trial_ending,
    "goodbye": _goodbye,
    "deadline_alert": _deadline_alert,
}


def render(kind, ctx, unsubscribe_url):
    """Render one email. Returns (subject, html, text).

    `ctx` is a plain dict of already-derived display values, never a raw users row — the
    template must not be the thing that decides how many days are left, or the console
    preview and the real send can disagree about the most important number in the email.
    """
    if kind not in _BUILDERS:
        raise ValueError(f"Unknown email kind: {kind!r}. Known: {', '.join(EMAIL_KINDS)}")
    subject, preheader, content, text, reason, unsub = _BUILDERS[kind](ctx or {},
                                                                       unsubscribe_url)
    return subject, _shell(subject, preheader, content, reason, unsub), text
