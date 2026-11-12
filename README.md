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

## Read-only external Google Sheets digest

Open **Profile & preferences → External Google Sheets digest** to compare jobs against a separate daily Google Sheet. This connection is independent of any Google account connected to Codex.

1. In a [Google Cloud project](https://console.cloud.google.com/), enable **Google Drive API** and **Google Sheets API**.
2. Configure the OAuth consent screen for your account. If it is in Testing mode, add yourself as a test user.
3. Create an OAuth client with application type **Desktop app** and download its JSON file.
4. Enter the Drive folder URL or ID containing your daily sheets. The default prefix **Jobs** matches **Jobs -- YYYY-MM-DD**, using the UK execution date. A single en dash or em dash separator is also recognised.
5. Optionally enter the exact jobs tab name when the sheet has several tabs.
6. Upload the client JSON, save the settings, choose **Connect Google**, and allow both read-only permissions.
7. Choose **Refresh index**. Google returns to this install's local port after authorisation.

The app requests Drive metadata read access and Sheets read access. It never edits or publishes your sheets. Credentials and refresh tokens stay under **external-digest/** in the private data directory, with owner-only file permissions.

Each collection refreshes the index, including CLI runs using the same data directory. All matching same-day sheets in the selected folder are checked. A recognised header-only jobs table is a verified empty index. Missing, inaccessible, ambiguous or malformed sources remain **Incomplete**. An old snapshot is never simply given a newer date.

Recognised columns include **Title**, **Company**, **Location**, **Apply URL**, **Job URL** and **Job ID**. Each populated row needs an ID, a job/apply URL, or all three title/company/location fields. External matches are excluded before hiring-contact research; deduplication across this app's boards remains separate.

Historical runs retain the comparison status recorded when they ran. If today's digest does not yet exist, its producer must run before a complete comparison is possible. Reconnect if Google expires the authorisation, which may happen more frequently while an OAuth app stays in Testing mode.

Implementation references: [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [Drive file search](https://developers.google.com/workspace/drive/api/guides/search-files), [Sheets values API](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get).

## Jobs by Qais

**Jobs by Qais** is available in the source picker and profile setup. Your search reads the public embedded jobs list at [jobsbyqais.coderco.io](https://jobsbyqais.coderco.io/#jobs), without a paid API or executing its marketing scripts.

The board is used for discovery. The original LinkedIn, Reed or employer vacancy is verified before a job qualifies. Board refresh dates, skill tags and entry-level labels are not proof of a newly posted vacancy or a hybrid arrangement. Date-only discovery values can prune clearly old candidates; exact-listing evidence still has to establish the configured posting window. Pay-to-train adverts and the existing trainee/no-experience title exclusions stay excluded. Links to the previously removed Indeed, Glassdoor, Welcome to the Jungle and Adzuna sources are skipped.

Verified hybrid jobs can be anywhere in the UK. The on-site city preference applies to on-site jobs, and remote roles still require UK eligibility. A city alone, a UK label or the word “hybrid” on a discovery page does not prove the arrangement. Jobs are merged with matching openings found through other sources and show **Jobs by Qais** as provenance. Missing dates, access failures and changed board data are recorded as incomplete or unverified rather than zero results.

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
