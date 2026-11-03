"""Reusable document template fed only by confirmed profile content."""
import base64
from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile


def extract_cv(encoded):
    import pymupdf as fitz

    if not isinstance(encoded, str):
        raise ValueError("Choose a PDF to import.")
    try:
        content = base64.b64decode(encoded, validate=True)
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass or not 1 <= len(pdf) <= 15:
                raise ValueError()
            text = "\n".join(page.get_text(sort=True) for page in pdf).strip()
    except (RuntimeError, ValueError, UnicodeError) as error:
        raise ValueError("Choose an unlocked PDF CV with at most 15 pages.") from error
    if not text:
        raise ValueError("This PDF has no selectable text. Enter your experience manually.")
    if len(text) > 18000:
        raise ValueError("The extracted text is too long. Paste your relevant CV sections manually.")
    return text


def render_pdf(html, page_limit, links=()):
    import pymupdf as fitz

    story = fitz.Story(html=html, user_css="""
        body { font-family: sans-serif; font-size: 11pt; color: #24212b; }
        h1 { font-size: 23pt; color: #624697; }
        h2 { font-size: 14pt; color: #624697; margin-bottom: 6pt; }
        p { margin-top: 5pt; margin-bottom: 8pt; }
    """)
    output = BytesIO()
    writer = fitz.DocumentWriter(output)
    bounds = fitz.paper_rect("a4")
    body = bounds + (48, 45, -48, -45)
    positions = []

    def record(position):
        position.page_num = page_number
        positions.append(position)

    for page_number in range(1, page_limit + 1):
        device = writer.begin_page(bounds)
        more, _ = story.place(body)
        if links:
            story.element_positions(record)
        story.draw(device)
        writer.end_page()
        if not more:
            break
    writer.close()
    if more:
        raise ValueError(f"The document exceeds {page_limit} page(s). Shorten the relevant profile sections.")
    with fitz.open(stream=output.getvalue(), filetype="pdf") as pdf:
        if links:
            fitz.Story.add_pdf_links(pdf, positions)
        if not 1 <= len(pdf) <= page_limit or any(not page.get_text().strip() for page in pdf):
            raise ValueError("The generated document failed its page checks.")
        content = pdf.tobytes(garbage=4, deflate=True)
    with fitz.open(stream=content, filetype="pdf") as pdf:
        actual_links = {link.get("uri") for page in pdf for link in page.get_links()}
        if not set(links) <= actual_links:
            raise ValueError("The generated CV failed its hyperlink check.")
        for page in pdf:
            page.get_pixmap(matrix=fitz.Matrix(.5, .5), alpha=False)
    return content


def html_text(value):
    return escape(value.replace("—", "-"))


def create_profile_bundle(profile, job, assessment):
    if not profile["evidence"]:
        raise ValueError("Add and confirm your experience in Profile & preferences before building documents.")
    rows = [row for row in assessment["requirements"] if row["match"] in {"Strong", "Good"}]
    matched = list(dict.fromkeys(row["requirement"] for row in rows))
    skills = list(dict.fromkeys(matched + profile["skills"]))
    name = html_text(profile["name"])
    contact = " · ".join(html_text(value) for value in (profile["city"], profile["email"]) if value)
    links = " · ".join(f'<a href="{html_text(link)}">{html_text(link)}</a>' for link in profile["links"])
    cv = f"<h1>{name}</h1><p><strong>{html_text(profile['title'])}</strong><br>{contact}</p>"
    if links:
        cv += f"<p>{links}</p>"
    sections = (("Skills", ", ".join(skills)), ("Experience", profile["evidence"]),
                ("Projects", profile["projects"]), ("Education", profile["education"]))
    for heading, content in sections:
        if content:
            cv += f"<h2>{heading}</h2><p>" + "<br>".join(map(html_text, content.splitlines())) + "</p>"

    role, company = html_text(job["title"]), html_text(job["company"])
    essential = [row["requirement"] for row in rows if not row["desirable"]]
    desirable = list(dict.fromkeys(row["requirement"] for row in rows if row["desirable"]))
    strengths = essential or matched
    sentence = f" My relevant experience includes {html_text(', '.join(strengths[:4]))}." if strengths else ""
    if desirable:
        sentence += f" I also bring experience with {html_text(', '.join(desirable[:4]))}, listed as desirable in the advert."
    letter = (f"<h1>{name}</h1><p>Hello {company} team,</p>"
              f"<p>I would welcome the opportunity to discuss the {role} role.{sentence}"
              f" My CV sets out the experience I would bring to your team.</p>"
              f"<p>Kind regards,<br>{name}</p>")
    stem = re.sub(r"[^\w-]+", "-", f"{profile['name']}-{job['company']}-{job['title']}").strip("-")[:110] or "Application"
    files = {f"{stem}-CV.pdf": render_pdf(cv, 2, profile["links"]), f"{stem}-Cover-Letter.pdf": render_pdf(letter, 1)}
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    with ZipFile(output) as archive:
        if archive.testzip() or set(archive.namelist()) != set(files):
            raise ValueError("The application ZIP failed its integrity check.")
    return {**files, f"{stem}-Application.zip": output.getvalue()}
