"""One local candidate profile, persisted with the dashboard's data."""
import re
from urllib.parse import urlsplit

from job_boards import SOURCES

MODELS = ("remote", "hybrid", "on-site")


def blank_profile():
    return dict(name="", title="", city="", roles=[], workModels=list(MODELS),
                sources=list(SOURCES), maxAgeHours=24, skills=[], evidence="", projects="",
                education="", email="", links=[], excludedEmployers=[], allowedAgencies=[],
                contactResearch=True, trackerUrl="", includeRecruiters=False)


def safe_link(value):
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def validate_profile(payload):
    profile = blank_profile()
    for key, default in profile.items():
        value = payload.get(key, default)
        if isinstance(default, list):
            if not isinstance(value, list) or len(value) > 60 or any(
                not isinstance(item, str) or len(item) > 350 or "\n" in item for item in value
            ):
                raise ValueError(f"Invalid list for {key}")
            profile[key] = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif isinstance(default, str):
            limit = 18000 if key in {"evidence", "projects", "education"} else 350
            if not isinstance(value, str) or len(value) > limit or ("\n" in value and limit == 350):
                raise ValueError(f"Invalid text for {key}")
            profile[key] = value.strip()
        elif type(value) is not type(default):
            raise ValueError(f"Invalid value for {key}")
        else:
            profile[key] = value
    if not all(profile[key] for key in ("name", "title", "city", "roles", "workModels", "sources")):
        raise ValueError("Enter your name, CV title and UK town or city, and select roles, work arrangements and sources.")
    if not set(profile["workModels"]) <= set(MODELS) or not set(profile["sources"]) <= set(SOURCES):
        raise ValueError("Choose supported work arrangements and sources.")
    if profile["maxAgeHours"] not in (24, 72, 168):
        raise ValueError("Choose a posting window of 24 hours, 3 days or 7 days.")
    if any(not safe_link(link) for link in profile["links"] + ([profile["trackerUrl"]] if profile["trackerUrl"] else [])):
        raise ValueError("Use full HTTPS addresses for profile and tracker links.")
    return profile


def role_pattern(role):
    return r"(?<!\w)" + r"[\s-]+".join(re.escape(word) for word in role.split()) + r"(?!\w)"


def build_config(profile):
    hours = profile["maxAgeHours"]
    city = profile["city"]
    models = profile["workModels"]
    return {
        "publishedAt": f"r{hours * 3600}",
        "roleVariants": profile["roles"],
        "searchLocations": {model: f"{city}, United Kingdom" if model == "on-site" else "United Kingdom"
                            for model in models},
        "maxJobsPerSearch": 100, "maxPagesPerSearch": 10, "maxJobsTotal": 1800,
        "bucketTargets": {model: 100 for model in models},
        "requestDelaySeconds": 8, "rateLimit": {"minIntervalSeconds": 8, "maxIntervalSeconds": 12},
        "jobSources": profile["sources"],
        "personalPolicy": {
            "version": "local-profile-v1", "roles": profile["roles"], "onsite_city": city,
            "work_models": models, "max_age_hours": hours,
            "allowed_agencies": profile["allowedAgencies"], "excluded_employers": profile["excludedEmployers"],
            "include_agencies": profile.get("includeRecruiters", False),
            "candidate": {"name": profile["name"], "title": profile["title"],
                          "location": f"{city}, United Kingdom", "skills": profile["skills"]},
            "verification": {"maxRequests": 3000, "maxPagesPerJob": 3, "requestDelaySeconds": 2},
            "contactResearch": {"enabled": profile["contactResearch"], "maxSearchesPerCompany": 2, "maxProfilesPerJob": 3},
        },
    }


def config_path(data):
    return data / "profile.json"

