"""
runner.py - one collector run, the same way for every data source.

  step 1   connect to the database, bring the tables up to date, record that the run started
  step 2+  the source's own steps, each printing one line
  end      totals, requests sent, advice, the run's record in the database, the verdict line

What stops a run and how it is reported:
  SiteStop        the site refused us, is down, or changed: that status (BLOCKED, ...)
  OutOfRequests   the run's request budget is used up: OK, the remaining steps are SKIPPED
  DatabaseError   DB_ERROR
  anything else   ERROR, with the technical details printed
Everything a step saved before it stopped stays saved; the next run carries on.

The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import sys
import traceback
from dataclasses import replace

from ..core.errors import OutOfRequests, SiteStop
from ..core.status import DB_ERROR, ERROR, NOT_RUN, OK, SKIPPED
from ..db.connection import DatabaseError
from ..web.describe import http_codes
from .steps import StepLog


class CollectorRunner:
    """storage: a RunStorage (the database); archive: keeps the page that stopped a run."""

    def __init__(self, definition, console, storage, archive, ran_on):
        self.definition = definition
        self.console = console
        self.storage = storage
        self.archive = archive
        self.ran_on = ran_on

    def run(self, source, context):
        """Run `source`. `context` is the run's RunContext; its database is opened here (step 1)."""
        definition, storage = self.definition, self.storage
        log = StepLog(definition.steps, self.console, definition.label_width, definition.shows_http_codes)
        status, failed_step, failure = OK, None, None
        opened = False
        run_id = work = None
        step = 1
        try:
            context = replace(context, db=storage.open(context.settings))
            opened = True
            storage.ensure_tables()
            work = source.start(context)
            detail = work.prepare()
            run_id = storage.start_run(self.ran_on, definition.key)
            log.done(1, OK, detail)

            work.open()
            for number, step_work in enumerate(work.steps(), start=2):
                step = number
                result = step_work()
                if isinstance(result, str):
                    log.done(number, OK, result)
                else:
                    log.done(number, OK, result.detail, result.codes)

        except SiteStop as stop:
            status, failed_step, failure = stop.status, step, stop.detail
            log.done(step, status, stop.detail, http_codes(stop.response))
            log.rest(step, NOT_RUN)
            if stop.response is not None:
                self.archive.keep(stop.response)
        except OutOfRequests:
            # the run just ends: nothing is wrong, the next run carries on
            log.done(step, OK, work.budget_used_up())
            log.rest(step, SKIPPED)
        except DatabaseError as error:
            status, failed_step, failure = DB_ERROR, step, str(error)
            log.done(step, DB_ERROR, failure)
            log.rest(step, NOT_RUN)
        except Exception as error:   # a bug, or something nobody expected
            status, failed_step, failure = ERROR, step, f"{type(error).__name__}: {error}"
            log.done(step, ERROR, failure)
            log.rest(step, NOT_RUN)
            self.console.line("\nTechnical details (useful for fixing the collector):")
            traceback.print_exc(file=sys.stdout)

        totals = None
        if opened and work is not None and status != DB_ERROR:
            try:
                totals = work.read_totals()
            except DatabaseError:
                pass

        if failure is not None:
            outcome = f"at step {failed_step} ({definition.steps[failed_step - 1]}) - {failure}"
        else:
            outcome = work.outcome(totals)
        verdict_line = f"VERDICT: {status} {outcome} (ran on {self.ran_on})"

        self.console.line()
        if work is not None:
            self.console.lines(work.closing_lines(totals))
            if work.requests_sent is not None:
                self.console.line(definition.requests_line(work.requests_sent))
        if status in definition.advice:
            self.console.lines(definition.advice[status])
        if opened and run_id is not None:
            try:
                storage.finish_run(run_id, status, verdict_line, {
                    "collector": definition.key,
                    "started_at_utc": context.started.isoformat(timespec="seconds"),
                    "steps": log.entries, "stats": work.stats, "totals": totals,
                    "requests": work.requests_sent or 0,
                })
            except DatabaseError:
                pass
        storage.close()
        self.console.line(verdict_line)
        return 0 if status == OK else 1
