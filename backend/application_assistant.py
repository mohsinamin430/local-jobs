"""Local, evidence-based job assessment and explicitly approved document creation."""
import re
from statistics import median
from urllib.parse import urlsplit

from contact_research import discover_and_verify_contact
from public_research import PublicResearch, PageParser, job_evidence, objects
from rate_control import LinkedInControl


KNOWN_REQUIREMENTS = (
    "AWS", "Terraform", "Docker", "ECS", "GitHub Actions", "CI/CD", "Linux", "Bash",
    "Networking", "Security scanning", "Observability", "PostgreSQL", "API testing",
    "Jira", "Confluence", "Agile", "Kubernetes", "EKS", "Helm", "Ansible", "Jenkins",
    "Azure", "GCP", "Python", "Go", "Java", "Software development", "Production architecture",
)
ALIASES = {
    "AWS": r"AWS|Amazon Web Services", "ECS": r"ECS|Fargate", "CI/CD": r"CI/CD|continuous (?:integration|delivery|deployment)",
    "Networking": r"networking|VPC|subnets|load balanc(?:er|ing)", "Security scanning": r"TFLint|Checkov|Trivy|security scanning",
    "Observability": r"observability|CloudWatch|Better Stack", "API testing": r"API testing|Postman|Bruno",
    "Agile": r"agile|scrum", "Kubernetes": r"Kubernetes|K8s", "GCP": r"GCP|Google Cloud", "Go": r"Golang|Go programming",
    "Production architecture": r"production architecture|enterprise architecture",
}
DESIRABLE = re.compile(r"^(?:desirable(?: skills| requirements| criteria)?|nice to have|preferred(?: qualifications| skills)?|bonus points)\s*[:\-]?", re.I)
SECTION = re.compile(r"^(?:essential|required|minimum qualifications|responsibilities|benefits|about us|how to apply)\b", re.I)
PAY = re.compile(r"£\s*\d[\d,]*(?:\.\d+)?\s*[kK]?(?:\s*(?:to|[-–])\s*£?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?)?")


def requirement_rows(description, profile):
    lines = (profile["evidence"] + "\n" + profile["projects"] + "\n" + profile["education"]).splitlines()
    vocabulary = {}
    confirmed = {skill.casefold(): skill for skill in profile["skills"]}
    for skill in list(dict.fromkeys([*KNOWN_REQUIREMENTS, *profile["skills"]])):
        if skill.casefold() in confirmed and confirmed[skill.casefold()] != skill:
            continue
        pattern = ALIASES.get(skill, re.escape(skill))
        evidence = next((line.strip() for line in lines if re.search(
            r"(?<![\w])(?:" + pattern + r")(?![\w])", line, re.I)), "")
        if evidence:
            vocabulary[skill] = ("Good", evidence[:350])
        elif skill.casefold() in confirmed:
            vocabulary[skill] = ("Partial", "Listed in your saved skills. No supporting example was entered.")
        else:
            vocabulary[skill] = ("Gap", "This requirement was not found in your saved profile.")
    rows = {}
    desirable = False
    for line in re.split(r"\n+|(?<=[.!?])\s+", description):
        line = line.strip(" •\t")
        if DESIRABLE.match(line):
            desirable = True
        elif SECTION.match(line):
            desirable = False
        for skill, (match, evidence) in vocabulary.items():
            pattern = ALIASES.get(skill, re.escape(skill))
            if re.search(r"\b(?:no|without)\s+(?:" + pattern + r")\b|(?:" + pattern + r")\s+(?:is\s+)?not required", line, re.I):
                continue
            if re.search(r"(?<![\w])(?:" + pattern + r")(?![\w])", line, re.I):
                key = (skill, desirable)
                rows.setdefault(key, {"requirement": skill, "quote": line[:350], "match": match,
                                      "experience": evidence, "desirable": desirable})
    return list(rows.values())


def annual_salary(posting):
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict) or salary.get("currency") != "GBP":
        return None
    value = salary.get("value")
    if not isinstance(value, dict) or str(value.get("unitText", "")).upper() not in {"YEAR", "YEARLY", "ANNUAL"}:
        return None
    low = value.get("minValue", value.get("value"))
    high = value.get("maxValue", value.get("value"))
    if all(type(amount) in {int, float} and 10_000 <= amount <= 500_000 for amount in (low, high)) and low <= high:
        return low, high
    return None


def format_salary(bounds):
    low, high = bounds
    return f"£{low:,.0f}–£{high:,.0f} per year" if low != high else f"£{low:,.0f} per year"


def evaluate(job, data, profile):
    research = PublicResearch(max_requests=10, delay=0, limiter=LinkedInControl(data))
    resolved = None
    if not job.get("title") or not job.get("company"):
        from vacancy_resolver import resolve_vacancy
        resolved = resolve_vacancy(job.get("url") or job.get("applyUrl"), research)
        for field in ("title", "company", "description", "url", "location", "workModel", "salary"):
            if resolved.get(field) and (not job.get(field) or field in {"title", "company", "description"}):
                job[field] = resolved[field]
        if resolved.get("apply_url"):
            job["applyUrl"] = resolved["apply_url"]
            job["apply_url"] = resolved["apply_url"]
    pasted = job.get("pasted", "").strip()
    description = pasted or job.get("description", "").strip()
    source = "Pasted vacancy text" if pasted else "Exact public listing" if resolved else "Saved listing"
    evidence = ([{"url": resolved["_evidence_url"], "status": "Exact vacancy identified"}] if resolved else [])
    posting = resolved.get("_posting", {}) if resolved else {}
    for url in ([] if resolved else dict.fromkeys([job.get("applyUrl"), job.get("url")])):
        if not url:
            continue
        try:
            body = research.get(url)
            exact = job_evidence(body, url, target=job)["job_postings"]
            if len(exact) == 1 and exact[0].get("description"):
                posting = exact[0]
                if not pasted:
                    description = PageParser(posting["description"]).text
                    source = "Exact public listing"
                evidence.append({"url": url, "status": "Exact vacancy matched"})
                break
            evidence.append({"url": url, "status": "Exact vacancy could not be confirmed"})
        except Exception:
            evidence.append({"url": url, "status": "Public page unavailable"})
    if len(description) < 100:
        raise ValueError("The exact job description is unavailable. Paste the vacancy text and try again.")
    rows = requirement_rows(description, profile)
    if not rows:
        recommendation = "Insufficient evidence"
    else:
        core = [row for row in rows if not row["desirable"]] or rows
        gaps = sum(row["match"] == "Gap" for row in core)
        matches = sum(row["match"] in {"Strong", "Good"} for row in core)
        if matches == 0 and gaps:
            recommendation = "Poor match"
        elif gaps or re.search(r"\b(?:senior|lead|principal|staff|head)\b", job["title"], re.I):
            recommendation = "Stretch opportunity"
        elif matches >= 4:
            recommendation = "Strong match"
        elif matches:
            recommendation = "Good match"
        else:
            recommendation = "Stretch opportunity"

    def excerpt(pattern):
        for line in re.split(r"\n+|(?<=[.!?])\s+", description):
            match = re.search(pattern, line, re.I)
            if match:
                return line[max(0, match.start()-60):match.end()+150].strip()
        return "Not stated in available text"

    details = {
        "Company": job["company"], "Position": job["title"], "Location": job.get("location") or "Not stated",
        "Work arrangement": excerpt(r"\b(?:hybrid (?:work(?:ing)?|role|arrangement)|on[- ]site|remote work(?:ing)?|fully remote|office attendance)\b"),
        "Employment type": excerpt(r"\b(?:permanent|fixed.term|contract type|inside IR35|outside IR35)\b"),
        "Job reference": excerpt(r"\b(?:job|vacancy|requisition)\s+(?:id|reference|ref)\b"),
        "Clearance and nationality": excerpt(r"\b(?:security clearance|SC clearance|DV clearance|BPSS|UK nationals?|British citizens?|nationality)\b"),
        "Travel": excerpt(r"\b(?:travel|commut(?:e|ing)|office days)\b"),
        "Closing date": excerpt(r"\b(?:closing date|applications close|apply by|application deadline)\b"),
    }
    for label, field in (("Employment type", "employmentType"), ("Job reference", "identifier"), ("Closing date", "validThrough")):
        value = posting.get(field)
        if isinstance(value, dict):
            value = value.get("value") or value.get("name")
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        if value and details[label] == "Not stated in available text":
            details[label] = str(value)
    details["Posting date"] = str(posting.get("datePosted") or posting.get("datePublished") or "Not stated")
    specific_clearance = excerpt(r"\b(?:Security Check\s*\(SC\)|Developed Vetting\s*\(DV\)|SC|DV)\s+clearance\b")
    if specific_clearance != "Not stated in available text":
        details["Security clearance"] = specific_clearance
    if details["Work arrangement"] == "Not stated in available text" and job.get("workModel") in {"remote", "hybrid", "on-site"}:
        details["Work arrangement"] = job["workModel"].capitalize() + (" (exact listing)" if resolved else " (saved listing)")
    pay = job.get("salary", "").strip()
    published = excerpt(r"(?:salary|base pay|day rate|per annum|per day).{0,100}£|£.{0,100}(?:salary|per annum|per day|a year)")
    if PAY.search(published):
        pay = published
    salary_status = "Saved listing value"
    if PAY.search(published):
        salary_status = "Vacancy text"
    elif resolved and resolved.get("salary"):
        salary_status = "Published on the matched vacancy"
    exact_salary = annual_salary(posting)
    if exact_salary:
        pay, salary_status = format_salary(exact_salary), "Published on the matched vacancy"
    salary_sources = []
    if not pay or pay.lower() in {"not stated", "not available"}:
        pay = "No verified salary available"
        salary_status = "No reliable estimate established"
        comparable = []
        for query, label in [(f'{job["company"]} {job["title"]} UK salary', "Company salary lead"),
                             (f'{job["title"]} UK salary benchmark', "UK market lead")]:
            try:
                for row in research.search(query):
                    if PAY.search(row.get("snippet", "")) and urlsplit(row.get("url", "")).scheme == "https":
                        salary_sources.append({"label": label, "url": row["url"], "title": row["title"],
                                               "excerpt": PageParser(row["snippet"]).text[:250], "verified": False})
                        page = PageParser(research.get(row["url"]))
                        for item in objects(page.json_blocks, "JobPosting"):
                            title = str(item.get("title", "")).strip().casefold()
                            location = str(item.get("jobLocation", ""))
                            bounds = annual_salary(item)
                            if bounds and title == job["title"].casefold() and re.search(r"United Kingdom|['\"]GB['\"]", location, re.I):
                                employer = item.get("hiringOrganization", {})
                                employer_name = str(employer.get("name", "")) if isinstance(employer, dict) else ""
                                if employer_name and not any(name.casefold() == employer_name.casefold() for name, _ in comparable):
                                    comparable.append((employer_name, bounds))
                                salary_sources[-1].update(verified=True, excerpt=format_salary(bounds),
                                                          label=f"Comparable vacancy at {employer_name or 'another employer'}")
                                break
                        break
            except Exception:
                continue
        if len(comparable) >= 2:
            pay = format_salary(tuple(median(bounds[i] for _, bounds in comparable) for i in (0, 1)))
            salary_status = "Indicative UK range from comparable adverts. Discuss the lower end if the role stretches your experience."
    contact = {"contact": "Not found", "contact_status": "incomplete", "verified_email": ""}
    if not job.get("imported") and job.get("contactStatus") == "verified":
        contact.update(contact=job.get("contact", "Not found"), contact_status="verified",
                       verified_email=job.get("email", ""))
    elif job.get("company") and profile["contactResearch"]:
        try:
            contact = discover_and_verify_contact(job, research.get, research.search,
                                                  {"maxSearchesPerCompany": 1, "maxProfilesPerJob": 1})
        except Exception:
            pass
    flags = []
    if details["Clearance and nationality"] != "Not stated in available text":
        flags.append("Check the stated clearance and nationality requirements before applying.")
    city = profile["city"]
    if details["Travel"] != "Not stated in available text" or (
            city.casefold() not in details["Location"].casefold() and re.search("hybrid|on.site", details["Work arrangement"], re.I)):
        flags.append(f"Confirm attendance frequency and travel feasibility from {city}.")
    if re.search(r"\b(?:senior|lead|principal|staff|head)\b", job["title"], re.I):
        flags.append("The title indicates senior scope. Check ownership and experience expectations.")
    if re.search(r"\b(?:upfront fee|pay to apply|purchase equipment|send bank details)\b", description, re.I):
        flags.append("The text contains a payment or financial-data request. Verify the vacancy with the employer.")
    strong = list(dict.fromkeys(row["requirement"] for row in rows if row["match"] in {"Strong", "Good"}))
    gaps = list(dict.fromkeys(row["requirement"] for row in rows if row["match"] in {"Gap", "Partial"} and not row["desirable"]))
    reason = ("Evidence aligns with " + ", ".join(strong[:5]) + ". " if strong else "")
    reason += ("Review " + ", ".join(gaps[:5]) + " against the role’s expectations. " if gaps else "")
    reason += "The comparison covers recognised requirements in the available description."
    return {"details": details, "requirements": rows, "recommendation": recommendation,
            "reason": reason,
            "source": source, "sources": evidence, "salary": pay, "salaryStatus": salary_status,
            "salarySources": salary_sources, "flags": flags, "contact": contact["contact"],
            "contactStatus": contact["contact_status"], "email": contact["verified_email"]}
