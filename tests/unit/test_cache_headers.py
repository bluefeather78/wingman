"""Cache-Control policy for app.main.

The blanket `no-store` this middleware used to apply to EVERY response was the load-time
font flash: `no-store` forbids the browser from keeping a response, so the seven
`<link rel="preload">` font tags `expo export -p web` writes into the document head paid
for a full download that could never be reused. The real @font-face fetch therefore
restarted from scratch after the 1.9MB bundle evaluated — measured on production
2026-08-24: preloads done by 566ms, the same four files re-fetched 1234ms->1466ms — so the
app painted its first text in the browser's default serif and swapped ~400ms later, on
every visit, since nothing was cached across loads either.

The exception is scoped to content-hashed build output and the hash is REQUIRED, never
assumed: handing `immutable, max-age=1y` to a file whose URL can be reused for different
bytes is not recoverable from the server side.
"""
from app.main import _is_immutable_asset


FONT = ("/assets/node_modules/@expo-google-fonts/space-grotesk/700Bold/"
        "SpaceGrotesk_700Bold.52e5e29a7805a81bac01a170e45d103d.ttf")
RETINA_PNG = ("/assets/node_modules/expo-router/assets/react-navigation/elements/"
              "clear-icon.c94f6478e7ae0cdd9f15de1fcb9e5e55@2x.png")
BUNDLE = "/_expo/static/js/web/entry-4215402965fe2a0875156369fe0bff13.js"


def test_hashed_font_is_immutable():
    assert _is_immutable_asset(FONT) is True


def test_hashed_retina_asset_is_immutable():
    """`@2x`/`@3x` follow the hash rather than a dot — the trailing class must admit both."""
    assert _is_immutable_asset(RETINA_PNG) is True


def test_hashed_js_bundle_is_immutable():
    """The bundle separates its hash with `-`, every other asset with `.`."""
    assert _is_immutable_asset(BUNDLE) is True


def test_unhashed_file_under_an_immutable_prefix_is_not():
    """`_expo/.routes.json` sits under /_expo/ with no hash; caching it for a year would
    pin routing to whatever shipped first."""
    assert _is_immutable_asset("/_expo/.routes.json") is False
    assert _is_immutable_asset("/assets/favicon.png") is False


def test_html_shells_are_never_immutable():
    """The shell names the current bundle hash, so a stale one pins a stale everything."""
    for path in ("/", "/index.html", "/tracker.html", "/terms.html", "/walkthrough.html"):
        assert _is_immutable_asset(path) is False, path


def test_api_and_root_static_are_never_immutable():
    for path in ("/api/opportunities", "/api/data/load", "/styles.css", "/favicon.ico"):
        assert _is_immutable_asset(path) is False, path


def test_hash_outside_the_build_prefixes_is_not_enough():
    """A hash-shaped name elsewhere on the host must not inherit the policy."""
    assert _is_immutable_asset(
        "/uploads/x.52e5e29a7805a81bac01a170e45d103d.ttf") is False
