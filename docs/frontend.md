# Accord frontend

The frontend is served at `/`, with the API explorer at `/docs`. HTML, CSS, JavaScript modules, icons, and example data ship in the Python wheel and Docker image.

## Connect a workspace

Create a tenant with `cip create-tenant`, open Accord, and choose **Connect workspace**. The API key establishes a session; the frontend uses a random HttpOnly, SameSite=Strict cookie for subsequent requests. Redis stores a hashed session reference with an eight-hour expiry. HTTPS responses set Secure cookies. Reloading restores the connected tenant; **Disconnect** revokes the session.

## Add and publish agreements

Choose **Add agreement** to upload TXT/DOCX files or paste agreement text. The interface shows admission and build progress, then presents a snapshot preview. **Publish snapshot** updates the workspace using an expected-active check. **Keep current version** leaves the existing published snapshot selected.

**Load sample set** submits the bundled examples through the same processing pipeline. The separately selected sample preview provides a local interactive introduction.

## Explore terms

The library filters by agreement/party text and review status. Opening an agreement shows its extracted terms and effective-date versions. Select a term or clause to inspect its source passages and full original document. Current and historical composites remain available together.

**Explore evidence** offers passage retrieval and numeric agreement counts. Live passage searches use backend retrieval. Live counts include coverage and a persisted receipt that can be opened from the result.

## Review changes

The review queue shows overlapping amendments and documents requiring attention. **Review change** opens the original instrument before a decision. Skipping an instrument's operations on a section creates a new build and preview; publication is explicit. Subsequent builds restore the published review resolutions and source exclusions.

## Activity

The activity view shows real job states, attempts, creation dates, and paginated processing history.

## Interface behavior

The layout adapts to desktop and narrow screens. Native controls provide keyboard navigation, visible focus, modal focus management, source disclosure, and live status messages. Reduced-motion preferences disable transitions. Displayed contract strings are HTML-escaped, and browser assets use a same-origin content security policy.
