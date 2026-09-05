"""The query classifier: does an angle's search DISCOVER or merely CONFIRM?

Every case here is a real query string from `agent_logs/scraper_20260823-*_seed*.json`, because
the only thing this heuristic has to be right about is the traffic it actually sees.
"""
from wingman import query_telemetry as qt


def shape(q):
    return qt.classify_query(q)["shape"]


class TestShape:
    def test_quoted_program_is_named(self):
        assert shape('"MassArt" "Summer Intensives" tuition cost eligibility high school') == "named"

    def test_unquoted_proper_noun_is_named(self):
        # The measurement this module exists for undercounts badly without this: half the
        # narrow queries name the institution without quoting it.
        assert shape("Johnson Wales University high school culinary summer camp") == "named"

    def test_class_description_is_broad(self):
        assert shape("high school neuroscience lab internship programs") == "broad"
        assert shape("landscape architecture high school summer program") == "broad"

    def test_subject_acronyms_are_not_program_names(self):
        # "AI", "UX", "STEM" are this catalog's subject vocabulary. Reading them as program
        # names would classify the broadest queries in the corpus as the narrowest.
        assert shape("responsible AI high school students program") == "broad"
        assert shape("UX UI design programs for high schoolers") == "broad"
        assert shape("high school STEM summer camps") == "broad"

    def test_real_acronym_is_named(self):
        assert shape("NASA high school internship") == "named"

    def test_metadata_only_when_nothing_is_named(self):
        assert shape("high school research internship cost eligibility stipend") == "metadata"
        # named wins the tie: "could only ever return one program" is the more important fact.
        assert shape('"BRAINYAC" "Columbia University" eligibility cost contact email') == "named"

    def test_flags_are_independent_of_shape(self):
        c = qt.classify_query("Seattle teen volunteer programs near me")
        assert c["shape"] == "broad" and c["audience"] and c["place"]
        assert not qt.classify_query("high school marine biology programs")["place"]


class TestSummarize:
    def test_breadth_is_the_broad_share(self):
        s = qt.summarize_queries([
            "high school marine biology programs",          # broad
            "high school oceanography research summer",      # broad
            '"Woods Hole" high school program cost',         # named
            "high school program cost eligibility",          # metadata
        ])
        assert s["total"] == 4
        assert s["counts"] == {"broad": 2, "named": 1, "metadata": 1}
        assert s["breadth"] == 0.5 and s["named_rate"] == 0.25

    def test_no_queries_is_none_not_zero(self):
        # A silent call and a narrowly-searching angle are different failures; 0% would say
        # they are the same one.
        s = qt.summarize_queries([])
        assert s["total"] == 0 and s["breadth"] is None and s["named_rate"] is None

    def test_blank_queries_are_dropped(self):
        assert qt.summarize_queries(["", "   ", None])["total"] == 0


class TestSummarizeSeed:
    def test_reads_a_real_log_shape(self):
        row = qt.summarize_seed({
            "angle": "national high school Theater programs",
            "searches": 5, "attempts": 1,
            "queries": ["high school theater summer programs",
                        '"Interlochen" theater high school cost'],
            "resolved_urls": ["https://a.example", "https://b.example"],
            "candidates": [{"name": "x"}, {"name": "y"}, {"name": "z"}],
        })
        assert row["searches"] == 5 and row["candidates"] == 3 and row["resolved_urls"] == 2
        assert row["breadth"] == 0.5
        assert not row["silent"] and not row["retried"]

    def test_silent_and_retried_are_reported(self):
        row = qt.summarize_seed({"searches": 0, "attempts": 2, "queries": []})
        assert row["silent"] and row["retried"] and row["breadth"] is None

    def test_a_half_written_log_degrades_to_a_thin_row(self):
        # These files are written by a run that can be killed part-way; one bad log must cost
        # one row, never the view.
        row = qt.summarize_seed({})
        assert row["angle"] == "" and row["total"] == 0 and row["candidates"] is None
        assert qt.summarize_seed(None)["total"] == 0


class TestSummarizeRun:
    def test_rates_are_over_queries_not_averaged_over_seeds(self):
        # An angle that issued 8 queries says more about the run than one that issued 1;
        # averaging the per-seed rates would weight them equally.
        big = qt.summarize_seed({"queries": ["high school biology programs"] * 1
                                            + ['"MIT" RSI cost'] * 7})
        small = qt.summarize_seed({"queries": ["high school art programs"]})
        run = qt.summarize_run([big, small])
        assert run["total_queries"] == 9
        assert run["counts"]["named"] == 7
        assert run["breadth"] == round(2 / 9, 3)   # not the mean of 0.125 and 1.0

    def test_distinct_queries_exposes_overlap_between_angles(self):
        a = qt.summarize_seed({"queries": ["high school robotics programs", "FIRST Robotics"]})
        b = qt.summarize_seed({"queries": ["High School Robotics Programs"]})
        run = qt.summarize_run([a, b])
        assert run["total_queries"] == 3 and run["distinct_queries"] == 2

    def test_queries_per_seed_ignores_seeds_that_logged_none(self):
        a = qt.summarize_seed({"queries": ["high school music programs", "high school choir"]})
        silent = qt.summarize_seed({"searches": 0, "queries": []})
        run = qt.summarize_run([a, silent])
        assert run["queries_per_seed"] == 2.0 and run["silent_seeds"] == 1

    def test_empty_run(self):
        run = qt.summarize_run([])
        assert run["seeds"] == 0 and run["breadth"] is None and run["queries_per_seed"] is None
