# Job Desk

Job Desk is a single-user dashboard for finding UK vacancies, comparing them with your experience, and creating a CV and cover letter. It runs on your machine with Docker Compose.

## 1. Install the prerequisites

- Install Docker Desktop on Windows or macOS, or Docker Engine with the Compose plugin on Linux.
- On Windows with WSL 2, start Docker Desktop and enable **Settings → Resources → WSL Integration** for your Linux distribution. Run the commands below in that distribution's terminal.
- Clone this repository or download and extract it. Open a terminal in the directory containing `compose.yaml`.

Check that Docker is running and Compose is available.

```sh
docker info
docker compose version
```

## 2. Start Job Desk

```sh
docker compose up -d --build dashboard
```

The first build downloads the base images and dependencies. When it finishes, open [http://127.0.0.1:8788/](http://127.0.0.1:8788/) in your browser. The dashboard listens on the local machine.

## 3. Set up your profile

On a new install, click **Start setup**.

1. Enter a UK town or city and the job titles you want to search for. Select work arrangements, sources, and how recently jobs must have been posted.
2. Enter your name, CV headline, and relevant skills. Paste your experience or import text from a PDF CV. Review the extracted text and add projects, education, and links in their respective fields.
3. Review your profile and click **Save profile**. You can edit it later under **Profile & preferences**.

## 4. Run your first search

On the job search screen, select **Quick check** for a small first run or **Full search** for broader coverage, then click **Run search**. Open a result to review the vacancy and how it matches your profile. Use **Build CV and cover letter** to generate application documents.

If a source limits access, check the source status in the dashboard. An incomplete source does not establish that there were no matching jobs.

## Optional browser rendering

To run the dashboard with its browser rendering service, use the Compose overlay.

```sh
docker compose -f compose.yaml -f compose.browser.yaml --profile browser up -d --build dashboard
```

## Check, stop, or update the app

```sh
docker compose ps
docker compose logs --tail=100 dashboard
docker compose stop
docker compose up -d --build dashboard
```

Use `ps` and `logs` if the page does not open. If startup reports a port conflict, free port `8788` and rerun the start command. After updating the source, rerun the build command to apply the changes. If you enabled browser rendering, use the overlay command above to rebuild.

Your saved profile and search history are stored in the `jobs-data` Docker volume. Stopping or rebuilding the dashboard preserves that volume. Removing the volume, for example with `docker compose down -v`, deletes the saved profile and history. Keep CVs, local settings, and exports out of Git.

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/` | Python dashboard API, job collector, browser service, and document generation |
| `web/` | React dashboard and frontend tests |
| `docker/` | Image build files and browser security profile |
| `config/` | Placeholder for optional local configuration |
| `compose.yaml` | Local services and data volume |
| `compose.browser.yaml` | Optional browser rendering overlay |

## Employer feeds

Select **Greenhouse** or **SmartRecruiters** in the dashboard to check employer career feeds.
The starter watchlist covers Monzo through Greenhouse and Version 1 through SmartRecruiters.
These are employer-specific feeds. Job Desk still checks the role, freshness, UK eligibility,
and work arrangement before including a vacancy.

## Search and application tools

The source picker includes LinkedIn, Totaljobs, Google, Greenhouse, SmartRecruiters, Jobicy, Lever, Ashby, Teamtailor, Workable, Remote OK and Jobs by Qais. Availability depends on public access and the selected employer watchlists. No paid scraping actor is required.

- Search boards concurrently. A rate-limited source waits and retries within the same run while other sources continue.
- Review saved runs or open a local job-results CSV in the browser. Imported values remain labelled as unverified.
- Use **Evaluate a listing** to paste an exact vacancy URL. If its description is inaccessible, use the advert-text fallback.
- Enhanced evaluation from a saved job opens the listing-evaluation page. Creating a CV and cover letter requires a separate button click after the assessment.
- Candidate skills, experience, projects, education, CV links, recruiter preferences and tracker link come from the local profile, which starts blank.
- Hybrid jobs can be anywhere in the UK. The configured home city limits on-site roles; remote roles still need UK eligibility. The chosen posting window is independently checked.
- Trainee, traineeship and generic no-experience-required titles are hidden. Genuine junior engineering vacancies remain eligible.

Greenhouse, SmartRecruiters, Lever, Ashby, Teamtailor and Workable use employer-specific watchlists rather than searching every company.

## Private data and Git

Candidate profiles, CV content, generated documents, application trackers, job exports, Google OAuth credentials and cached external indexes belong in the private data volume or ignored local directories. A fresh install contains no candidate identity or experience.

The ignore rules cover local data, reports, environment files, credentials, CV/document files, spreadsheet trackers and common exports. Ignore rules do not remove a file that was previously committed. Check both staged changes and history before publishing private material, and never commit real credentials as examples.

Git author information and the configured remote are repository metadata. They may identify the person making commits even when all application data is generic.

## Tests

From the repository root in WSL or Linux:

```sh
PYTHONPATH=backend python3 -m unittest discover -s tests
```

Backend tests use synthetic candidate/employer data and mocked Google responses. They require no real Google credentials. Frontend tests and TypeScript checks run during the Docker image build.
