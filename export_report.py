"""Describe a build run without touching Tk or guessing from old output files.

The source .love archive is useful, but is not a successful desktop/Web export.
Results count requested platforms only. A leftover file from an older run is
never reported as a new success: only artifacts returned by this worker count.
"""

from dataclasses import dataclass
from pathlib import Path

from branding import APP_NAME, APP_VERSION, CREATOR_CREDIT


PLATFORM_FOLDERS = {"Windows": "Windows", "Mac": "macOS", "Linux": "Linux", "Web": "Web"}
PLATFORM_LABELS = {"Source": ".love archive", "Windows": "Windows", "Mac": "macOS", "Linux": "Linux", "Web": "Web"}


@dataclass(frozen=True)
class BuildOutcome:
    platform: str
    status: str  # done, failed, or not_built
    artifact: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class ExportSummary:
    state: str  # success, partial, or failed
    title: str
    detail: str
    succeeded: int
    requested: int


def build_outcomes(
    platforms: tuple[str, ...],
    artifacts: tuple[Path, ...],
    failure_messages: dict[str, str],
    archive_error: str | None,
) -> tuple[BuildOutcome, ...]:
    """Map the current worker's outputs and failures to one row per stage."""
    source = next((path for path in artifacts if path.suffix.lower() == ".love"), None)
    outcomes = [BuildOutcome("Source", "done", source) if source else
                BuildOutcome("Source", "failed", error=archive_error or "The game archive was not created.")]
    for platform in platforms:
        if platform in failure_messages:
            outcomes.append(BuildOutcome(platform, "failed", error=failure_messages[platform]))
            continue
        artifact = next((path for path in artifacts
                         if path.parent.name == PLATFORM_FOLDERS[platform]
                         and path.suffix.lower() != ".exe"), None)
        outcomes.append(BuildOutcome(platform, "done", artifact) if artifact else
                        BuildOutcome(platform, "not_built", error="Not run because an earlier stage could not finish."))
    return tuple(outcomes)


def summarize_export(outcomes: tuple[BuildOutcome, ...], archive_error: str | None = None) -> ExportSummary:
    """Do not call an all-platform failure 'complete' just because Source exists."""
    targets = [outcome for outcome in outcomes if outcome.platform != "Source"]
    succeeded = sum(outcome.status == "done" for outcome in targets)
    requested = len(targets)
    if requested and succeeded == requested and not archive_error:
        return ExportSummary("success", "Your builds are ready", f"{succeeded} of {requested} platforms exported. Share the complete packages.", succeeded, requested)
    if succeeded:
        return ExportSummary("partial", "Some builds need attention", f"{succeeded} of {requested} platforms exported. Successful builds are ready to use; view Details for the rest.", succeeded, requested)
    return ExportSummary("failed", "Export needs attention", "No platform build was completed. View Details, fix the reported issue, and try again.", 0, requested)


def format_build_report(
    game_name: str,
    developer: str,
    release_dir: Path,
    outcomes: tuple[BuildOutcome, ...],
    archive_error: str | None = None,
) -> str:
    """Readable saved/copyable report, using package-relative artifact names."""
    summary = summarize_export(outcomes, archive_error)
    lines = [f"{game_name} — Build report", "=" * 48,
             f"Export tool: {APP_NAME} {APP_VERSION}", CREATOR_CREDIT,
             f"Game developer / studio: {developer or 'Not specified'}", "",
             summary.title, summary.detail, "", "RESULTS"]
    for outcome in outcomes:
        label = PLATFORM_LABELS[outcome.platform]
        status = {"done": "EXPORTED", "failed": "FAILED", "not_built": "NOT BUILT"}[outcome.status]
        lines.append(f"\n[{status}] {label}")
        if outcome.artifact:
            try:
                name = str(outcome.artifact.relative_to(release_dir))
            except ValueError:
                name = outcome.artifact.name
            lines.append(f"  {name}")
        if outcome.error:
            lines.append(outcome.error)
    if archive_error:
        lines.extend(["", "RUN ERROR", archive_error])
    lines.extend(["", "HOW TO SHARE",
                  "Windows: share the whole RAR/ZIP or game folder, not the EXE alone.",
                  "macOS: extract on a Mac. Public releases need appropriate signing/notarization.",
                  "Linux: extract the whole tar.gz on x86_64 Linux and run ./AppRun.",
                  "Web: host the whole Web folder via HTTP(S), or use Preview Web.",
                  "Source .love needs LÖVE; the desktop packages already include a runtime.",
                  "Keep CREDITS.txt, licenses/, and original third-party notices with each package.",
                  "The exporter's MIT license does not select or replace the game's license."])
    return "\n".join(lines) + "\n"
