# Job Desk

Job Desk is a single-user job search dashboard for UK vacancies. It searches public sources, compares listings with a locally saved candidate profile, and can export a CV and cover letter.

## Run locally

Install Docker with Compose, then run from the repository directory.

```sh
docker compose up -d --build dashboard
```

Open [Job Desk](http://127.0.0.1:8788/) and choose **Start setup**. Save your target roles, work preferences and CV evidence to run your first search.

For optional browser rendering, use:

```sh
docker compose -f compose.yaml -f compose.browser.yaml --profile browser up -d --build dashboard
```

Your profile and search history live in the local Docker data volume. Use **Profile & preferences** to edit them. Keep CVs, local settings, and exports out of Git.
