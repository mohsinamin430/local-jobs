"""Jobs by Qais discovery and exact-vacancy/location regressions."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from qais_feed import BOARD_URL, QaisFeed, board_data
from additional_feeds import FEEDS
from browser_client import needs_render
from job_feeds import matches_role
from personal_rules import evaluate_job, dedup_keys

NOW = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
LINK = "https://uk.linkedin.com/jobs/view/junior-devops-engineer-at-example-4489007105"
ROW = {"title":"Junior DevOps Engineer","company":"Example Ltd","location":"Manchester, England, United Kingdom",
       "url":LINK,"source":"LinkedIn","posted":"2026-11-12","flags":[]}
DESCRIPTION = ("This is a hybrid role in the UK. Build AWS ECS Fargate infrastructure using Terraform, Docker, Linux, "
               "GitHub Actions CI/CD and VPC networking. One year of experience is sufficient.")
EMPLOYER = {"name":"Example Ltd","description":"Example develops and operates a cloud software platform for its own business customers.",
            "url":"https://www.linkedin.com/company/example/about/"}


def html(rows, generated="2026-11-12T15:00:00Z"):
    return '<html><script>const DATA = ' + json.dumps({"meta":{"generated":generated,"location":"United Kingdom"},"jobs":rows}) + ';window.mustNeverRun=true;</script></html>'


def advert(description=DESCRIPTION, posted="2 hours ago", location="Manchester, England, United Kingdom", company="Example Ltd"):
    return ('<h2 class="top-card-layout__title">Junior DevOps Engineer</h2>'
            f'<a class="topcard__org-name-link" href="https://www.linkedin.com/company/example">{company}</a>'
            f'<span class="topcard__flavor--bullet">{location}</span>'
            f'<span class="posted-time-ago__text">{posted}</span>'
            f'<div class="show-more-less-html__markup">{description}</div>')


class QaisFeedTests(unittest.TestCase):
    def setUp(self):
        self.feed = QaisFeed()
        self.plan = next(self.feed.plans([], {}))

    def card(self, row=None):
        cards, _, _ = self.feed.page(self.plan, 0, lambda _: html([row or deepcopy(ROW)]))
        return cards[0]

    def research(self, body):
        return Mock(get=Mock(return_value=body), checkpoint=SimpleNamespace(observed_at=lambda _: NOW.isoformat()))

    def test_public_embedded_data_is_read_without_rendering_or_executing_javascript(self):
        body = html([ROW])
        cards, consumed, more = self.feed.page(self.plan, 0, lambda _: body)
        self.assertEqual((consumed,more),(1,False))
        self.assertEqual(cards[0]["id"],"jobsbyqais:4489007105")
        self.assertEqual(cards[0]["linkedin_job_id"],"4489007105")
        self.assertEqual(cards[0]["feed_search_date"],"2026-11-12")
        self.assertNotIn("structured_dates",cards[0])
        self.assertNotIn("listing_work_model",cards[0])
        self.assertFalse(needs_render(BOARD_URL,body))
        self.assertIsInstance(FEEDS["jobsbyqais"],QaisFeed)

    def test_empty_jobs_is_zero_but_missing_or_malformed_payload_is_not(self):
        cards, consumed, more = self.feed.page(self.plan,0,lambda _:html([]))
        self.assertEqual((cards,consumed,more),([],0,False))
        for body in ("<html>Challenge</html>",'<script>const DATA = {broken};</script>',
                     '<script>const DATA = {"jobs":[{"title":"bad"}]};</script>'):
            with self.subTest(body=body),self.assertRaises(ValueError):
                board_data(body)
            self.assertTrue(needs_render(BOARD_URL,body))

    def test_removed_boards_pay_to_train_and_duplicate_links_are_not_fetched(self):
        rows=[deepcopy(ROW),{**ROW,"url":LINK+"?utm_source=duplicate"},
              {**ROW,"url":"https://uk.indeed.com/viewjob?jk=123"},
              {**ROW,"url":"https://www.adzuna.co.uk/jobs/details/123"},
              {**ROW,"url":"https://www.glassdoor.co.uk/job-listing/123"},
              {**ROW,"url":"https://www.welcometothejungle.com/en/companies/example/jobs/123"},
              {**ROW,"url":"https://careers.example.com/jobs/course","flags":["⚠ likely pay-to-train course"]}]
        cards,consumed,_=self.feed.page(self.plan,0,lambda _:html(rows))
        self.assertEqual(consumed,len(rows))
        self.assertEqual(len(cards),1)

    def test_board_refresh_and_location_never_replace_missing_exact_evidence(self):
        job=self.card()
        research=self.research(advert(posted="",location=""))
        self.feed.detail(job,self.plan,research)
        job["employer_evidence"]=EMPLOYER
        self.assertEqual(job["location"],"")
        self.assertEqual(evaluate_job(job,{},NOW)["decision"],"unverified")
        self.assertEqual(job.get("structured_dates"),[])

    def test_exact_hybrid_roles_across_uk_cities_are_retained(self):
        for city in ("Manchester, England, United Kingdom","London, England, United Kingdom",
                     "Cardiff, Wales, United Kingdom","Belfast, Northern Ireland, United Kingdom"):
            with self.subTest(city=city):
                job=self.card()
                research=self.research(advert(location=city))
                self.feed.detail(job,self.plan,research)
                job["employer_evidence"]=EMPLOYER
                result=evaluate_job(job,{},NOW)
                self.assertEqual(result["decision"],"retain",result)
                self.assertEqual(result["work_model"],"hybrid")
                self.assertEqual(job["location"],city)
                self.assertEqual(research.get.call_args.args[0],
                    "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4489007105")

    def test_onsite_outside_home_city_and_non_uk_hybrid_still_reject(self):
        for description,location in (
            (DESCRIPTION.replace("hybrid","on-site"),"Manchester, England, United Kingdom"),
            (DESCRIPTION,"Paris, France")):
            with self.subTest(location=location):
                job=self.card()
                self.feed.detail(job,self.plan,self.research(advert(description=description,location=location)))
                job["employer_evidence"]=EMPLOYER
                self.assertEqual(evaluate_job(job,{"onsite_city":"Birmingham"},NOW)["decision"],"reject")

    def test_exact_stale_date_overrides_recent_discovery_and_observation_uses_cached_time(self):
        job=self.card()
        self.feed.detail(job,self.plan,self.research(advert(posted="3 days ago")))
        job["employer_evidence"]=EMPLOYER
        self.assertEqual(job["detail_observed_at"],NOW.isoformat())
        self.assertEqual(evaluate_job(job,{"onsite_city":"Birmingham"},NOW)["decision"],"reject")

    def test_wrong_employer_and_title_fail_verification(self):
        for body in (advert(company="Unrelated Ltd"),advert().replace("Junior DevOps Engineer","Cloud Engineer")):
            with self.subTest(body=body),self.assertRaises(ValueError):
                self.feed.detail(self.card(),self.plan,self.research(body))

    def test_cross_board_identity_and_existing_title_exclusions_are_preserved(self):
        job=self.card()
        self.assertIn("linkedin:4489007105",dedup_keys(job))
        self.assertTrue(set(dedup_keys(job)) & set(dedup_keys({"id":"4489007105"})))
        context=SimpleNamespace(config={},policy={})
        self.assertFalse(matches_role(context,"Trainee DevOps Engineer - no experience needed"))
        self.assertTrue(matches_role(context,"Junior DevOps Engineer"))


class QaisPipelineTests(unittest.TestCase):
    def test_curated_hybrid_job_reaches_results_with_exact_relative_date(self):
        from pathlib import Path
        import tempfile
        import contextlib
        import io
        from unittest.mock import patch
        import collector as c
        import personal_pipeline as pipeline

        settings = {"publishedAt":"r86400","roleVariants":["DevOps Engineer"],"jobSources":["jobsbyqais"],
                    "maxPagesPerSearch":1,"maxJobsPerSearch":5,"maxJobsTotal":5,"saveOnlyUniqueItems":False,
                    "personalPolicy":{"roles":["DevOps Engineer"],"onsite_city":"Birmingham",
                                      "work_models":["hybrid"],"contactResearch":{"enabled":False},
                                      "verification":{"maxRequests":10,"requestDelaySeconds":0}}}
        row = {**ROW,"posted":datetime.now(timezone.utc).date().isoformat()}
        research = Mock(requests=0,records=[])
        research.get.side_effect = lambda url: html([row]) if url==BOARD_URL else advert()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(pipeline,"PublicResearch",return_value=research), \
             patch.object(pipeline,"enrich_job",side_effect=lambda job,*_: {**job,"employer_evidence":EMPLOYER}):
            root=Path(temporary)
            with contextlib.redirect_stdout(io.StringIO()):
                c.run(settings,root)
            latest=json.loads((root/"latest.json").read_text())
            jobs=json.loads((Path(latest["directory"])/"jobs.json").read_text())
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["work_model"],"hybrid")
        self.assertEqual(jobs[0]["source"],"jobsbyqais")
        self.assertEqual(jobs[0]["found_on"],["jobsbyqais"])
        self.assertTrue(any(item["field"]=="detail_posted_text" for item in jobs[0]["freshness_evidence"]))
