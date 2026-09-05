"""Classifier queue dry-run: the pure hub-lead mapping (classify -> hub-mining lead). Hermetic."""
from wingman import classify_page
from wingman import discovered_leads as dl
from agents import classify_queue as cq


def test_first_party_hub_becomes_same_domain_lead():
    lead = cq._hub_lead({"id": "ec1", "name": "X Index", "url": "https://cmu.edu/pre-college"},
                        classify_page.CLASS_FIRST_PARTY_HUB)
    assert lead["kind"] == dl.KIND_HUB and lead["scope"] == dl.SCOPE_SAME_DOMAIN
    assert lead["url"] == "https://cmu.edu/pre-college" and lead["status"] == dl.STATUS_NEW


def test_third_party_hub_becomes_off_domain_lead():
    lead = cq._hub_lead({"id": "ec2", "name": "20 Best Programs", "url": "https://blog.com/list"},
                        classify_page.CLASS_THIRD_PARTY_HUB)
    assert lead["scope"] == dl.SCOPE_OFF_DOMAIN


def test_hub_scope_map_only_covers_the_two_hub_classes():
    assert set(cq._HUB_SCOPE) == {classify_page.CLASS_FIRST_PARTY_HUB,
                                  classify_page.CLASS_THIRD_PARTY_HUB}
    assert classify_page.CLASS_PROGRAM not in cq._HUB_SCOPE
    assert classify_page.CLASS_NONE not in cq._HUB_SCOPE
