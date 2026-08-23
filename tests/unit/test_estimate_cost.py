"""Unit tests for the two estimate_cost() ports, tested SEPARATELY.

gemini_common and claude_common carry DIFFERENT price constants and Claude additionally
prices cache-creation/cache-read tokens and uses a different per-search fee. Expected
values are computed FROM each module's own constants (never a hardcoded literal the
source could drift away from) so a constant change forces a corresponding test change
rather than passing silently.
"""
import claude_common
import gemini_common


# ===========================================================================
# gemini_common.estimate_cost
# ===========================================================================
class TestGeminiEstimateCost:
    def test_input_and_output_tokens_only(self):
        usage = {"input_tokens": 1000, "output_tokens": 2000}
        expected = (
            1000 * gemini_common.INPUT_PRICE_PER_TOKEN
            + 2000 * gemini_common.OUTPUT_PRICE_PER_TOKEN
        )
        assert gemini_common.estimate_cost(usage) == expected

    def test_with_web_searches_adds_per_search_fee(self):
        usage = {
            "input_tokens": 500,
            "output_tokens": 300,
            "server_tool_use": {"web_search_requests": 3},
        }
        expected = (
            500 * gemini_common.INPUT_PRICE_PER_TOKEN
            + 300 * gemini_common.OUTPUT_PRICE_PER_TOKEN
            + 3 * gemini_common.WEB_SEARCH_PRICE_PER_SEARCH
        )
        assert gemini_common.estimate_cost(usage) == expected

    def test_search_fee_is_the_dominant_lever(self):
        """Isolate the per-search fee: a single search alone equals the constant."""
        usage = {"server_tool_use": {"web_search_requests": 1}}
        assert gemini_common.estimate_cost(usage) == gemini_common.WEB_SEARCH_PRICE_PER_SEARCH

    def test_zero_usage_is_zero_cost(self):
        assert gemini_common.estimate_cost({}) == 0.0

    def test_missing_server_tool_use_defaults_to_no_searches(self):
        usage = {"input_tokens": 100, "output_tokens": 0}
        expected = 100 * gemini_common.INPUT_PRICE_PER_TOKEN
        assert gemini_common.estimate_cost(usage) == expected

    def test_gemini_does_not_price_cache_tokens(self):
        """Gemini's estimator ignores cache_* keys entirely (only Claude prices them)."""
        base = {"input_tokens": 100, "output_tokens": 100}
        with_cache = dict(base, cache_creation_input_tokens=9999,
                          cache_read_input_tokens=9999)
        assert gemini_common.estimate_cost(with_cache) == gemini_common.estimate_cost(base)


# ===========================================================================
# claude_common.estimate_cost
# ===========================================================================
class TestClaudeEstimateCost:
    def test_input_and_output_tokens_only(self):
        usage = {"input_tokens": 1000, "output_tokens": 2000}
        expected = (
            1000 * claude_common.INPUT_PRICE_PER_TOKEN
            + 2000 * claude_common.OUTPUT_PRICE_PER_TOKEN
        )
        assert claude_common.estimate_cost(usage) == expected

    def test_cache_creation_and_read_tokens_are_priced_as_input(self):
        """Cache-write and cache-read tokens are billed and fold into the input side."""
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 50,
        }
        expected = (
            (100 + 200 + 300) * claude_common.INPUT_PRICE_PER_TOKEN
            + 50 * claude_common.OUTPUT_PRICE_PER_TOKEN
        )
        assert claude_common.estimate_cost(usage) == expected

    def test_cache_tokens_alone_still_cost(self):
        usage = {"cache_read_input_tokens": 1000}
        expected = 1000 * claude_common.INPUT_PRICE_PER_TOKEN
        assert claude_common.estimate_cost(usage) == expected

    def test_with_web_searches_adds_per_search_fee(self):
        usage = {
            "input_tokens": 500,
            "output_tokens": 300,
            "server_tool_use": {"web_search_requests": 2},
        }
        expected = (
            500 * claude_common.INPUT_PRICE_PER_TOKEN
            + 300 * claude_common.OUTPUT_PRICE_PER_TOKEN
            + 2 * claude_common.WEB_SEARCH_PRICE_PER_SEARCH
        )
        assert claude_common.estimate_cost(usage) == expected

    def test_zero_usage_is_zero_cost(self):
        assert claude_common.estimate_cost({}) == 0.0

    def test_missing_server_tool_use_defaults_to_no_searches(self):
        usage = {"input_tokens": 100, "output_tokens": 0}
        expected = 100 * claude_common.INPUT_PRICE_PER_TOKEN
        assert claude_common.estimate_cost(usage) == expected


# ===========================================================================
# Cross-module: the constants genuinely differ, which is why the two are
# priced separately. If these ever converge, the "price with the provider that
# served the call" comment in app.core.record_interactive_cost is moot.
# ===========================================================================
class TestConstantsDiffer:
    def test_per_token_rates_differ_between_providers(self):
        # Gemini gemini-3.6-flash: $0.75 / $3.75 per MTok.
        assert gemini_common.INPUT_PRICE_PER_TOKEN == 0.75 / 1_000_000
        assert gemini_common.OUTPUT_PRICE_PER_TOKEN == 3.75 / 1_000_000
        # Claude Haiku 4.5: $1.00 / $5.00 per MTok.
        assert claude_common.INPUT_PRICE_PER_TOKEN == 1 / 1_000_000
        assert claude_common.OUTPUT_PRICE_PER_TOKEN == 5 / 1_000_000
        assert (gemini_common.INPUT_PRICE_PER_TOKEN
                != claude_common.INPUT_PRICE_PER_TOKEN)

    def test_per_search_fee_differs_between_providers(self):
        # Gemini: $14 / 1000 grounded requests = $0.014. Claude: $0.01 flat.
        assert gemini_common.WEB_SEARCH_PRICE_PER_SEARCH == 14 / 1000
        assert claude_common.WEB_SEARCH_PRICE_PER_SEARCH == 0.01
        assert (gemini_common.WEB_SEARCH_PRICE_PER_SEARCH
                != claude_common.WEB_SEARCH_PRICE_PER_SEARCH)
