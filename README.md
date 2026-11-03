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
