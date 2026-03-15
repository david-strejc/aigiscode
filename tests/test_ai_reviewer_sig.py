import inspect
from aigiscode.review.ai_reviewer import review_findings


def test_review_findings_accepts_primary_backend():
    sig = inspect.signature(review_findings)
    assert "primary_backend" in sig.parameters
