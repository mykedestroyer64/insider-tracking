"""
The interfaces the collectors' steps are written against (sources/ports.py), and the real
things that stand behind them: every repository and site gateway offers each method its
interface names, with the same arguments, so a run works with the real one and with a
test's stand-in alike.
"""
import inspect
import sys

from insider_tracking.db.congress import CongressReports
from insider_tracking.db.europe import MarTrades
from insider_tracking.db.fx import FxRates
from insider_tracking.db.insiders import InsiderFilings
from insider_tracking.db.returns import TradeReturns
from insider_tracking.db.size import DatabaseSize
from insider_tracking.db.state import StateStore
from insider_tracking.sources import ports

from .support.checks import Checks

check = Checks()
IMPLEMENTATIONS = [    # (interface, the real thing behind it)
    (ports.Bookmarks, StateStore),
    (ports.ReportStore, CongressReports),
    (ports.InsiderStore, InsiderFilings),
    (ports.MarStore, MarTrades),
    (ports.RateStore, FxRates),
    (ports.ReturnStore, TradeReturns),
    (ports.SizeGauge, DatabaseSize),
]


def members(port):
    """The methods and properties an interface asks for."""
    found = {}
    for klass in reversed(port.__mro__):
        if klass in (object,) or klass.__name__ in ("Protocol", "Generic"):
            continue
        for name, value in vars(klass).items():
            if name.startswith("_"):
                continue
            if callable(value) or isinstance(value, property):
                found[name] = value
    return found


def parameters(function):
    """(name, has a default) of each parameter after self."""
    return [(p.name, p.default is not inspect.Parameter.empty, p.kind)
            for p in list(inspect.signature(function).parameters.values())[1:]]


def fits(port, implementation):
    """The members of `port` that `implementation` lacks or takes differently (empty: it fits)."""
    problems = []
    for name, wanted in members(port).items():
        offered = getattr(implementation, name, None)
        if offered is None:
            problems.append(f"no {name}")
            continue
        if isinstance(wanted, property):
            if not isinstance(inspect.getattr_static(implementation, name), property):
                problems.append(f"{name} is not a property")
            continue
        want, have = parameters(wanted), parameters(offered)
        if [p[:2] for p in have[:len(want)]] != [p[:2] for p in want]:
            problems.append(f"{name}{inspect.signature(offered)} instead of {name}{inspect.signature(wanted)}")
        elif any(not has_default and kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                 for _, has_default, kind in have[len(want):]):
            problems.append(f"{name} needs more arguments than its interface gives")
    return problems


check.section("each implementation fits its interface")
for port, implementation in IMPLEMENTATIONS:
    problems = fits(port, implementation)
    check(f"{implementation.__module__}.{implementation.__name__} fits {port.__name__}", not problems, problems)

check.section("the check itself catches a misfit")


class Wrong:
    def get(self):
        return None


misfit = fits(ports.Bookmarks, Wrong)
check("a class without the right methods doesn't fit",
      len(misfit) == 2 and misfit[0].startswith("get(self) instead of get(self, key)") and misfit[1] == "no set", misfit)
sys.exit(check.finish())
