r"""
---------------------------------------------------------------------------
Copyright (c) PC2Lab Development Team
All rights reserved.

This file is part of free(dom) software -- you can redistribute it
and/or modify it under the terms of the GNU General Public
License (GPL)as published by the Free Software Foundation, either
version 3 (GPL v3), or (at your option) a later version.

The software is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

Miami University and PC2Lab makes no representations or warranties
about the suitability of the software, either express or implied,
including but not limited to the implied warranties of
merchantability, fitness for a particular purpose, or
non-infringement.  Miami University and PC2Lab is not be liable for
any damages suffered by licensee as a result of using, result of
using, modifying or distributing this software or its derivatives.

By using or copying this Software, Licensee agrees to abide by the
intellectual property laws, and all other applicable laws of the
U.S., and the terms of this license.

Authors: Stefan Stojanovski      stojansz@miamiOH.edu

---------------------------------------------------------------------------

Everything the program does that leaves the process: HTTP downloads, unzipping,
and running the external GDAL / osmium tools.

The download path is the one piece with no direct counterpart in the C++
version.  ``cpr::Download`` there restarted a failed transfer from byte zero,
which is painful when the Geofabrik extract for a large state is 1.3 GB and the
connection drops at 90%.  This implementation does what ``curl -C -`` does in
``fetch_city_data.sh``: it streams into a ``.part`` file and, if that file
already holds bytes, asks the server to continue from where it stopped.
"""

import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests

from .config import Config
from .logger import get_logger

logger = get_logger(__name__)

# Streamed in 1 MiB pieces.  Large enough that the per-chunk overhead vanishes
# against the syscall, small enough that a 1.3 GB extract never materialises in
# memory -- which matters here more than usual, because several producers are
# streaming their own multi-gigabyte file at the same time.
CHUNK_SIZE = 1 << 20

# Connect timeout, then read timeout.  The read timeout is per-chunk, not for
# the whole transfer, so a slow-but-alive 20-minute download is fine while a
# genuinely dead socket is noticed within a minute.
TIMEOUT_SECONDS = (10, 60)


class FetchUtility:
    """HTTP downloads, archive extraction, and external-tool invocation.

    One instance is shared by every worker thread.  The only per-thread state
    is the ``requests.Session``: sessions are not documented as thread-safe, so
    each thread gets its own through ``threading.local()``.  That still keeps
    the benefit of a session -- connection pooling and keep-alive against
    census.gov, which every producer hits five times -- without sharing a
    connection pool across threads.
    """

    def __init__(self, config: Config) -> None:
        """Remember the settings that govern retries and identification.

        Args:
            config: the loaded configuration.
        """
        self.config = config
        self._thread_state = threading.local()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    @property
    def session(self) -> requests.Session:
        """This thread's HTTP session, created on first use.

        Returns:
            A requests.Session private to the calling thread.
        """
        session = getattr(self._thread_state, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": self.config.user_agent,
                    # Range requests are in terms of the bytes actually sent.
                    # If the server were to gzip the response, the offsets we
                    # resume from would refer to the compressed stream while
                    # requests hands us the decompressed one, and the resumed
                    # file would be silently corrupt.  Asking for identity
                    # encoding removes the whole class of problem; these are
                    # already-compressed archives, so nothing is lost.
                    "Accept-Encoding": "identity",
                }
            )
            self._thread_state.session = session

        return session

    def download(self, url: str, destination: Path) -> bool:
        """Download a URL to a path, resuming a partial transfer if there is one.

        The contract is the one the rest of the program relies on: when this
        returns, ``destination`` either does not exist or is a *complete* file.
        There is no state in which a truncated download is mistaken for a
        cached one, because bytes only ever land in ``destination`` through an
        atomic rename from the ``.part`` file.

        Callers check the file rather than this return value, since False means
        both "already cached" and "failed" -- deliberately, as it did in the
        C++ version.

        Args:
            url: the resource to fetch.
            destination: where the completed file is placed.

        Returns:
            True if this call actually transferred the file; False if it was
            already cached or could not be obtained.
        """
        if destination.exists() and destination.stat().st_size > 0:
            logger.info("cached   %s", destination.name)
            return False

        destination.parent.mkdir(parents=True, exist_ok=True)
        part = Path(f"{destination}.part")

        logger.info("GET      %s", url)

        for attempt in range(1, self.config.max_retry + 1):
            try:
                if self._attempt_download(url, part, attempt):
                    # atomic: a reader either sees no file or the whole file,
                    # never a half-written one.  os.replace overwrites on POSIX
                    # and Windows alike, unlike Path.rename on the latter.
                    os.replace(part, destination)
                    logger.info(
                        "done     %s (%d bytes)",
                        destination.name,
                        destination.stat().st_size,
                    )
                    return True
            except (requests.RequestException, OSError) as error:
                logger.warning(
                    "attempt %d/%d failed for %s: %s",
                    attempt,
                    self.config.max_retry,
                    url,
                    error,
                )

            if attempt < self.config.max_retry:
                time.sleep(self.config.retry_delay_seconds)

        # The .part file is deliberately left on disk.  fetch_city_data.sh
        # deletes it here, but keeping it means a later run of the program
        # resumes a part-downloaded state extract instead of starting the whole
        # multi-gigabyte transfer again.  Nothing else reads a .part, so it
        # cannot be mistaken for a usable file in the meantime.
        logger.error("download failed: %s", url)
        return False

    def _attempt_download(self, url: str, part: Path, attempt: int) -> bool:
        """Make one HTTP attempt, appending to or restarting the .part file.

        Args:
            url: the resource to fetch.
            part: the partial file this transfer accumulates into.
            attempt: 1-based attempt number, for the log only.

        Returns:
            True if ``part`` now holds the complete resource.

        Raises:
            requests.RequestException: on any transport-level failure.
            OSError: if the partial file cannot be written.
        """
        resume_from = part.stat().st_size if part.exists() else 0

        headers = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            logger.info(
                "resume   %s from byte %d (attempt %d)", part.name, resume_from, attempt
            )

        with self.session.get(
            url, headers=headers, stream=True, timeout=TIMEOUT_SECONDS
        ) as response:
            # 416 means the range starts at or past the end of the resource,
            # i.e. the .part already holds every byte there is.  A previous run
            # transferred everything and then died before the rename.
            if response.status_code == requests.codes.requested_range_not_satisfiable:
                if resume_from > 0:
                    logger.info("resume   %s was already complete", part.name)
                    return True
                response.raise_for_status()

            if response.status_code == requests.codes.partial_content:
                mode = "ab"
            elif response.status_code == requests.codes.ok:
                # the server ignored the Range header and is sending the whole
                # resource, so the partial file has to be thrown away rather
                # than appended to
                if resume_from > 0:
                    logger.info(
                        "resume   %s not honoured by server, restarting", part.name
                    )
                mode = "wb"
                resume_from = 0
            else:
                response.raise_for_status()
                # a 2xx that is neither 200 nor 206 (204, 205) carries no body
                raise requests.RequestException(
                    f"unexpected status {response.status_code} for {url}"
                )

            expected_total = self._expected_total(response, resume_from)

            with open(part, mode) as out_file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        out_file.write(chunk)

        # A connection that drops mid-transfer does not always raise: the
        # response can simply end early.  Comparing against the length the
        # server promised is what catches that, and because the bytes so far
        # are still in the .part file, the next attempt resumes rather than
        # restarts.
        written = part.stat().st_size
        if expected_total is not None and written != expected_total:
            raise requests.RequestException(
                f"truncated transfer: {written} of {expected_total} bytes"
            )

        if written == 0:
            raise requests.RequestException("server returned an empty body")

        return True

    @staticmethod
    def _expected_total(response: requests.Response, resume_from: int) -> int | None:
        """Work out how large the finished file should be, if the server says.

        Args:
            response: the streaming response, headers already received.
            resume_from: byte offset this transfer started at.

        Returns:
            The expected final size of the .part file, or None when the server
            did not say (a chunked response has no Content-Length).
        """
        # A 206 carries "Content-Range: bytes 500-999/1000"; the figure after
        # the slash is the size of the whole resource, which is exactly what
        # the finished .part must match.
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[1].strip()
            if total.isdigit():
                return int(total)

        # Otherwise Content-Length describes only this response's body, so the
        # offset it started from has to be added back.
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit():
            return resume_from + int(content_length)

        return None

    # ------------------------------------------------------------------
    # archives
    # ------------------------------------------------------------------

    def unzip(self, zip_path: Path, destination: Path) -> bool:
        """Unpack a zip archive.

        Args:
            zip_path: the archive to unpack.
            destination: directory its contents land in.

        Returns:
            True if the archive was unpacked successfully.
        """
        # -o overwrite existing files without prompting
        # -q quiet; the per-file listing is noise for a few thousand PUMS rows
        # -d destination directory
        return self.run_command(
            ["unzip", "-o", "-q", str(zip_path), "-d", str(destination)]
        )

    def fetch_archive(
        self, url: str, zip_path: Path, destination: Path, sentinel: str
    ) -> bool:
        """Download an archive unless it is already unpacked, then unpack it.

        Caching is keyed off a *sentinel*: a file the archive is known to
        contain.  If that file is already on disk, nothing is fetched and
        nothing is unpacked.  That is what makes a re-run, or a second city
        from a state a previous run already covered, essentially free.

        Args:
            url: archive URL.
            zip_path: where the archive itself is kept.
            destination: directory the archive is unpacked into.
            sentinel: file name, relative to ``destination``, proving the
                archive has already been unpacked.

        Returns:
            True if ``sentinel`` exists once the call returns.
        """
        unpacked = destination / sentinel

        # already unpacked by an earlier run, or by an earlier city in this state
        if unpacked.exists():
            logger.info("cached   %s", unpacked.name)
            return True

        # download() returns False both for "already on disk" and for "failed",
        # so the archive itself is what gets checked, not the return value
        self.download(url, zip_path)
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            logger.error("could not obtain %s", url)
            return False

        destination.mkdir(parents=True, exist_ok=True)
        if not self.unzip(zip_path, destination):
            logger.error("could not unzip %s", zip_path)
            return False

        if not unpacked.exists():
            logger.error("%s did not contain %s", zip_path, sentinel)
            return False

        return True

    # ------------------------------------------------------------------
    # external tools
    # ------------------------------------------------------------------

    @staticmethod
    def quote(command: list[str]) -> str:
        """Render an argument list the way a shell would have to be given it.

        Only ever used for log messages.  The commands themselves are executed
        as argument lists and never go through a shell, so there is no quoting
        for a path with a space or a quote in it to get wrong -- which is the
        whole reason the C++ version needed its own ``fetch::quote``.

        Args:
            command: the argument list.

        Returns:
            A copy-pasteable command line.
        """
        return shlex.join(command)

    def run_command(self, command: list[str]) -> bool:
        """Run an external tool and wait for it to finish.

        Output is captured rather than left attached to this process.  With
        several consumers running ogr2ogr and osmium at once, inherited stderr
        would interleave into an unreadable mess; capturing it means a failure
        reports its own diagnostics as one attributable block.

        Args:
            command: the argument list, e.g. ["ogr2ogr", "-f", "GeoJSON", ...].

        Returns:
            True if the command exited with status 0.
        """
        logger.debug("run      %s", self.quote(command))

        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except OSError as error:
            logger.error("could not run %s: %s", self.quote(command), error)
            return False

        if completed.returncode != 0:
            logger.error(
                "command failed (exit %d): %s", completed.returncode, self.quote(command)
            )
            self._log_tool_output(completed.stdout, completed.stderr, logger.error)
            return False

        # GDAL and osmium warn on stderr about things that are not failures
        # (unrecognised fields, missing .prj); worth keeping, not worth showing
        self._log_tool_output(completed.stdout, completed.stderr, logger.debug)
        return True

    def capture_command(self, command: list[str]) -> str:
        """Run an external tool and collect its standard output.

        Args:
            command: the argument list.

        Returns:
            Everything the command wrote to stdout; empty if it could not be
            started or exited non-zero.
        """
        logger.debug("capture  %s", self.quote(command))

        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except OSError as error:
            logger.error("could not run %s: %s", self.quote(command), error)
            return ""

        if completed.returncode != 0:
            logger.warning("command failed: %s", self.quote(command))
            self._log_tool_output("", completed.stderr, logger.debug)
            return ""

        return completed.stdout

    @staticmethod
    def have_command(tool: str, hint: str) -> bool:
        """Check that an external tool is reachable on PATH.

        Args:
            tool: name of the executable, e.g. "ogr2ogr".
            hint: advice logged when the tool is missing.

        Returns:
            True if the tool was found.
        """
        if shutil.which(tool) is not None:
            return True

        logger.error("'%s' not found on PATH. %s", tool, hint)
        return False

    @staticmethod
    def _log_tool_output(stdout: str, stderr: str, log) -> None:
        """Emit an external tool's captured output, one record per stream.

        Args:
            stdout: the tool's standard output.
            stderr: the tool's standard error.
            log: the logger method to emit through, e.g. ``logger.error``.
        """
        if stdout.strip():
            log("  stdout: %s", stdout.strip())
        if stderr.strip():
            log("  stderr: %s", stderr.strip())
