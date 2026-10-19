"""A Gemini 429 is either the daily quota or a per-minute limit — they must not be confused."""

from google.genai import errors

from nexus.llm.gemini import _map_api_error


def _429(quota_id: str, retry: str | None = "39s") -> errors.APIError:
    details = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests", "quotaId": quota_id}],
        }
    ]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    body = {"error": {"code": 429, "message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED", "details": details}}
    return errors.APIError(429, body)


def test_per_minute_limit_is_a_short_pause():
    e = _map_api_error(_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))
    assert e.kind == "rate_limit" and e.retry_after_s == 39


def test_daily_quota_is_quota():
    assert _map_api_error(_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier")).kind == "quota"


def test_unknown_429_never_switches_the_ai_off_for_the_day():
    e = _map_api_error(errors.APIError(429, {"error": {"code": 429, "message": "Resource exhausted"}}))
    assert e.kind == "rate_limit" and e.retry_after_s == 30
