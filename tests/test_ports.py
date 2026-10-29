"""
The interfaces the collectors' steps are written against (sources/ports.py), and the real
things that stand behind them: every repository, site gateway and the request budget offers
each method and attribute its interface names, with the same arguments, so a run works with
the real one and with a test's stand-in alike.
"""
import inspect
import sys
from types import SimpleNamespace

from insider_tracking.db.congress import CongressReports
from insider_tracking.db.europe import MarTrades
from insider_tracking.db.fx import FxRates
from insider_tracking.db.insiders import InsiderFilings
from insider_tracking.db.returns import TradeReturns
from insider_tracking.db.size import DatabaseSize
from insider_tracking.db.state import StateStore
from insider_tracking.sources import ports
from insider_tracking.sources.ecb.site import EcbSite
from insider_tracking.sources.house.site import HouseSite
from insider_tracking.sources.sec.site import SecSite
from insider_tracking.sources.senate.site import EfdSite
from insider_tracking.sources.sharadar.site import SharadarApi
from insider_tracking.sources.sweden.site import FiRegister
from insider_tracking.web.pacing import RequestBudget

from .support.checks import Checks

check = Checks()
CLIENT = SimpleNamespace()        # the gateways only keep their web client, so any object will do
IMPLEMENTATIONS = [    # (interface, how to make the real thing behind it)
    (ports.Budget, lambda: RequestBudget(10)),
    (ports.Bookmarks, lambda: StateStore(None)),
    (ports.SizeGauge, lambda: DatabaseSize(None)),
    (ports.ReportList, lambda: CongressReports(None, "senate")),
    (ports.ReportQueueStore, lambda: CongressReports(None, "senate")),
    (ports.ReportResults, lambda: CongressReports(None, "house")),
    (ports.InsiderStore, lambda: InsiderFilings(None)),
    (ports.MarStore, lambda: MarTrades(None)),
    (ports.RateStore, lambda: FxRates(None)),
    (ports.ReturnStore, lambda: TradeReturns(None)),
    (ports.EfdGateway, lambda: EfdSite(CLIENT)),
    (ports.HouseGateway, lambda: HouseSite(CLIENT)),
    (ports.SecGateway, lambda: SecSite(CLIENT)),
    (ports.RegisterGateway, lambda: FiRegister(CLIENT)),
    (ports.RatesGateway, lambda: EcbSite(CLIENT)),
    (ports.PriceApi, lambda: SharadarApi(CLIENT, "key")),
]


def members(port):
    """The methods, properties and attributes an interface asks for: {name: what}."""
    found = {}
    for klass in reversed(port.__mro__):
        if klass is object or klass.__name__ in ("Protocol", "Generic"):
            continue
        for name in getattr(klass, "__annotations__", {}):
            found[name] = "attribute"
        for name, value in vars(klass).items():
            if not name.startswith("_") and (callable(value) or isinstance(value, property)):
                found[name] = value
    return found


def parameters(function):
    """(name, has a default, kind) of each parameter after self."""
    return [(p.name, p.default is not inspect.Parameter.empty, p.kind)
            for p in list(inspect.signature(function).parameters.values())[1:]]


def fits(port, thing):
    """What `thing` lacks or takes differently from `port` (empty: it fits)."""
    problems = []
    for name, wanted in members(port).items():
        if not hasattr(thing, name):
            problems.append(f"no {name}")
            continue
        if wanted == "attribute":
            continue
        offered = inspect.getattr_static(thing, name)
        if isinstance(wanted, property):
            if not isinstance(offered, property):
                problems.append(f"{name} is not a property")
            continue
        method = getattr(type(thing), name)
        want, have = parameters(wanted), parameters(method)
        if [p[:2] for p in have[:len(want)]] != [p[:2] for p in want]:
            problems.append(f"{name}{inspect.signature(method)} instead of {name}{inspect.signature(wanted)}")
        elif any(not has_default and kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                 for _, has_default, kind in have[len(want):]):
            problems.append(f"{name} needs more arguments than its interface gives")
    return problems


check.section("each real thing fits its interface")
for port, make in IMPLEMENTATIONS:
    thing = make()
    problems = fits(port, thing)
    check(f"{type(thing).__module__}.{type(thing).__name__} fits {port.__name__}", not problems, problems)
    check(f"  and isinstance(..., {port.__name__}) says so", isinstance(thing, port))

check.section("the check itself catches a misfit")


class Wrong:
    def get(self):
        return None


misfit = fits(ports.Bookmarks, Wrong())
check("a class without the right methods doesn't fit",
      len(misfit) == 2 and misfit[0].startswith("get(self) instead of get(self, key)") and misfit[1] == "no set", misfit)
check("and isinstance() doesn't take it for one", not isinstance(Wrong(), ports.Bookmarks))
sys.exit(check.finish())
