#!/usr/bin/env python3
"""Render legal/*.md into standalone public/terms.html / public/privacy.html pages.

The repo has no build step and no markdown dependency, so rather than teach the
browser to parse markdown, the two legal documents are kept as markdown (the form
they were authored in, and the form that is easy to redline) and rendered to static
HTML once, here. **Re-run this after editing anything in legal/** — the .html files
are generated artifacts, don't hand-edit them:

    python -m agents.build_legal

Only the markdown subset those two documents actually use is supported: ATX
headings, `- ` bullets, `**bold**`, `---` rules, and paragraphs. Anything fancier
would need a real parser; if a future edit needs it, add it here rather than
hand-patching the HTML.
"""
import html
import re

DOCS = [
    ("legal/terms.md", "public/terms.html", "Terms of Use", "terms"),
    ("legal/privacy.md", "public/privacy.html", "Privacy Policy", "privacy"),
]

# The navy brand mark, shared by the header and footer (sized by the wrapping <svg>).
LOGO_SVG = """<circle cx="71" cy="32" r="13" fill="#FACC15" opacity="0.35"></circle>
          <rect x="24" y="60" width="10" height="16" rx="2" fill="#F97316"></rect>
          <rect x="38" y="52" width="10" height="24" rx="2" fill="#F97316"></rect>
          <rect x="52" y="44" width="10" height="32" rx="2" fill="#F97316"></rect>
          <rect x="66" y="36" width="10" height="40" rx="2" fill="#F97316"></rect>
          <circle cx="71" cy="32" r="6.5" fill="#FACC15"></circle>"""

# Header nav links, matching the marketing pages. The current page is rendered with the
# `active` class (white); build_header() takes which one is current.
NAV_LINKS = [
    ("Pricing", "/pricing.html", "pricing"),
    ("How we use AI", "/how-we-use-ai.html", "how-we-use-ai"),
    ("FAQ", "/faq.html", "faq"),
    ("About", "/about.html", "about"),
    ("Terms", "/terms.html", "terms"),
    ("Privacy", "/privacy.html", "privacy"),
]


def build_header(active):
    links = "\n".join(
        '          <a class="navlink%s" href="%s">%s</a>'
        % (" active" if key == active else "", href, label)
        for label, href, key in NAV_LINKS
    )
    menu_links = "\n".join(
        '        <a class="menu-link%s" href="%s">%s</a>'
        % (" active" if key == active else "", href, label)
        for label, href, key in NAV_LINKS
    )
    return f"""  <!-- Header pill -->
  <div style="width:100%;max-width:1100px;margin:0 auto;padding:16px 24px 0 24px;position:sticky;top:16px;z-index:50;">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;background:#1D4E89;border-radius:999px;padding:8px 16px;box-shadow:0 10px 25px -5px rgba(29,78,137,0.45);position:relative;">
      <input type="checkbox" id="nav-toggle" class="menu-toggle" aria-hidden="true" tabindex="-1">
      <a href="/" style="display:flex;align-items:center;gap:8px;">
        <svg width="30" height="30" viewBox="0 0 100 100">
          {LOGO_SVG}
        </svg>
        <span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:16px;color:#fff;">Wingman</span>
        <span style="background:#F79256;border-radius:999px;padding:3px 9px;">
          <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:9px;color:#fff;letter-spacing:0.5px;">BETA</span>
        </span>
      </a>
      <div style="display:flex;align-items:center;gap:18px;">
        <div class="navlinks">
{links}
        </div>
        <a href="/login" style="display:flex;align-items:center;gap:6px;padding-right:8px;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="4" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></circle><path d="M4 20c0-4 4-6 8-6s8 2 8 6" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></svg>
          <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:14px;color:#fff;opacity:0.9;">Sign In</span>
        </a>
        <label for="nav-toggle" class="hamburger" aria-label="Open menu">
          <span class="hb-bar"></span><span class="hb-bar"></span><span class="hb-bar"></span>
        </label>
      </div>
      <div class="mobile-menu">
{menu_links}
      </div>
    </div>
  </div>"""


FOOTER = f"""  <!-- Footer -->
  <div style="width:100%;max-width:1100px;margin:0 auto;padding:32px 24px 32px 24px;border-top:1px solid #E2E8F0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
    <a href="/" style="display:flex;align-items:center;gap:8px;">
      <svg width="20" height="20" viewBox="0 0 100 100">
        {LOGO_SVG}
      </svg>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12px;color:#64748B;">Wingman</span>
    </a>
    <div style="display:flex;gap:20px;">
      <a href="/terms.html" style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12px;color:#64748B;">Terms</a>
      <a href="/privacy.html" style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12px;color:#64748B;">Privacy</a>
    </div>
    <span style="width:100%;font-family:'Plus Jakarta Sans',sans-serif;font-weight:500;font-size:12px;line-height:18px;color:#94A3B8;">Highschool Wingman is a doing-business-as (DBA) name of Blufeather Labs LLC. &copy; 2026 Blufeather Labs LLC.</span>
  </div>"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Wingman</title>
<link rel="icon" href="favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800&display=swap" rel="stylesheet">
<!-- Generated by agents/build_legal.py from {source} — do not edit by hand. -->
<style>
  *{{box-sizing:border-box;}}
  body{{margin:0;background:#FBF8F3;font-family:'Plus Jakarta Sans',system-ui,sans-serif;}}
  a{{text-decoration:none;}}
  .navlink{{font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:13px;color:#B7D3E8;}}
  .navlink:hover{{color:#fff;}}
  .navlink.active{{color:#fff;}}
  /* Mobile nav: a CSS-only hamburger (checkbox toggle, no JS) mirroring the app's
     landing header. The secondary links collapse behind it below 768px. */
  .navlinks{{display:flex;align-items:center;gap:18px;flex-wrap:wrap;}}
  .menu-toggle{{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;}}
  .hamburger{{display:none;flex-direction:column;gap:4px;cursor:pointer;padding:8px;margin-left:4px;}}
  .hb-bar{{display:block;width:20px;height:2px;border-radius:1px;background:#fff;}}
  .mobile-menu{{display:none;}}
  .menu-link{{padding:11px 16px;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:14px;color:#1D4E89;}}
  .menu-link.active{{color:#F79256;}}
  @media (max-width:767px){{
    .navlinks{{display:none;}}
    .hamburger{{display:flex;}}
    .menu-toggle:checked ~ .mobile-menu{{display:flex;flex-direction:column;position:absolute;top:calc(100% + 8px);right:0;min-width:190px;background:#fff;border:2px solid #1D4E89;border-radius:14px;padding:6px 0;box-shadow:0 10px 24px rgba(15,23,42,0.2);z-index:60;}}
  }}
  .legal-doc h1{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:32px;line-height:40px;color:#1D4E89;margin:0 0 24px 0;}}
  .legal-doc h2{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:22px;line-height:30px;color:#1D4E89;margin:32px 0 16px 0;}}
  .legal-doc h3{{font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:16px;line-height:24px;color:#1A2540;margin:24px 0 10px 0;}}
  .legal-doc p{{font-family:'Plus Jakarta Sans',sans-serif;font-weight:500;font-size:16px;line-height:27px;color:#1A2540;margin:0 0 16px 0;}}
  .legal-doc ul{{margin:0 0 16px 0;padding-left:24px;}}
  .legal-doc li{{font-family:'Plus Jakarta Sans',sans-serif;font-weight:500;font-size:16px;line-height:27px;color:#1A2540;margin-bottom:6px;}}
  .legal-doc strong{{font-weight:700;}}
  .legal-doc a{{color:#6A63E8;font-weight:700;}}
  .legal-doc a:hover{{color:#F79256;}}
  .legal-doc hr{{border:none;border-top:1px solid #E2E8F0;margin:32px 0;}}
</style>
</head>
<body>

<div style="min-height:100vh;background:#FBF8F3;overflow-x:hidden;">

{header}

  <!-- Article -->
  <div style="width:100%;max-width:1100px;margin:0 auto;padding:56px 24px 80px 24px;">
    <div style="background:#fff;border-radius:22px;padding:48px;box-shadow:0 2px 18px rgba(15,23,42,0.06);">
      <article class="legal-doc">
{body}
      </article>
    </div>
  </div>

{footer}

</div>

</body>
</html>
"""

# Stands in for a markdown two-space hard break between the line-splitting pass and
# the inline pass, which is where paragraph lines have already been joined.
HARD_BREAK = chr(0xE000)  # a private-use codepoint, so it cannot collide with document text


def inline(text):
    """Escape, then re-apply the inline markup we allow (bold, links, two-space breaks)."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    # `[label](href)` links. Escape already ran, so href/label are safe to inline; an
    # http(s) link is opened in a new tab (external, so we don't navigate away from the
    # policy) with rel=noopener. Kept deliberately simple — no titles, no nested markup.
    def _link(m):
        label, href = m.group(1), m.group(2)
        ext = ' target="_blank" rel="noopener noreferrer"' if href.startswith("http") else ""
        return f'<a href="{href}"{ext}>{label}</a>'
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, out)
    # A markdown hard break (a line that ended in two spaces) arrives here as the
    # HARD_BREAK sentinel, because paragraph lines were joined before this ran.
    return out.replace(HARD_BREAK, "<br>").strip()


def render(md):
    lines = md.split("\n")
    out, bullets, para = [], [], []

    def flush_para():
        if para:
            out.append('        <p>%s</p>' % inline(" ".join(para)))
            para.clear()

    def flush_bullets():
        if bullets:
            out.append('        <ul>')
            out.extend('          <li>%s</li>' % inline(b) for b in bullets)
            out.append('        </ul>')
            bullets.clear()

    for raw in lines:
        line = raw.rstrip() + (HARD_BREAK if raw.endswith("  ") and raw.strip() else "")
        if not line.strip():
            flush_para()
            flush_bullets()
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            flush_para()
            flush_bullets()
            level = len(heading.group(1))
            out.append('        <h%d>%s</h%d>' % (level, inline(heading.group(2)), level))
            continue
        if line.strip() == "---":
            flush_para()
            flush_bullets()
            out.append('        <hr>')
            continue
        bullet = re.match(r"^\s*-\s+(.*)$", line)
        if bullet:
            flush_para()
            bullets.append(bullet.group(1))
            continue
        flush_bullets()
        para.append(line.strip())

    flush_para()
    flush_bullets()
    return "\n".join(out)


def main():
    for source, target, title, active in DOCS:
        with open(source, encoding="utf-8") as fh:
            md = fh.read()
        page = PAGE.format(title=title, source=source, body=render(md),
                           header=build_header(active), footer=FOOTER)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(page)
        print(f"{source} -> {target} ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
