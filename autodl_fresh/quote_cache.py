"""Regional reference quotes; real orders always use a new website quote."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json

from .models import compact, now
from .rental import money

FILE = "gpu-quotes.json"


@lru_cache(maxsize=1)
def bundled_quotes():
    data = json.loads(files("autodl_fresh").joinpath("data/gpu_quotes.json").read_text(encoding="utf-8"))
    return data["regions"]


def region_name(source):
    region = source["region"] if isinstance(source, dict) else source.region
    return compact(region.split("/")[0])


class QuoteCache:
    def __init__(self, store):
        self.store = store
        self.bundled = bundled_quotes()

    def _local(self):
        try:
            data = self.store.read(FILE, {})
            if isinstance(data, dict) and isinstance(data.get("regions"), dict):
                return data
        except RuntimeError:
            pass  # A damaged local cache does not discard the bundled quotes.
        return {"regions": {}}

    def _all(self):
        regions = deepcopy(self.bundled)
        for entry in regions.values():
            entry["quote_origin"] = "bundled"
        for key, entry in self._local()["regions"].items():
            if not isinstance(entry, dict):
                continue
            names = entry.get("gpu_names")
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names):
                continue
            prices = entry.get("gpu_prices", {})
            if not isinstance(prices, dict):
                continue
            previous = regions.get(key, {})
            regions[key] = {**previous, **entry, "quote_origin": "local",
                            "gpu_prices": {**previous.get("gpu_prices", {}), **prices}}
        return {"regions": regions}

    def get(self, source):
        entry = self._all()["regions"].get(region_name(source), {})
        names = entry.get("gpu_names", [])
        if not names or not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            return None
        prices = {}
        quotes = entry.get("gpu_prices", {})
        for name, price in (quotes if isinstance(quotes, dict) else {}).items():
            try:
                low, high = money(price["min"]), money(price["max"])
                if name in names and low <= high:
                    prices[name] = {"min": str(low), "max": str(high)}
            except (KeyError, TypeError, ValueError):
                continue
        return {**entry, "source": source if isinstance(source, dict) else source.to_dict(),
                "gpu_names": list(dict.fromkeys(names)), "gpu_prices": prices, "from_cache": True}

    def update(self, data, *, complete=False):
        state = self._all()
        key = region_name(data["source"])
        previous = state["regions"].get(key, {})
        names = data["gpu_names"]
        prices = {name: price for name, price in previous.get("gpu_prices", {}).items() if name in names}
        prices.update(data.get("gpu_prices", {}))
        dates = {name: date for name, date in previous.get("price_checked_at", {}).items() if name in names}
        timestamp = now().isoformat()
        dates.update({name: timestamp for name in data.get("gpu_prices", {})})
        entry = {**previous, "quote_origin": "local", "gpu_names": names, "gpu_prices": prices, "price_checked_at": dates,
                 "price_errors": data.get("price_errors", {}), "updated_at": timestamp}
        if complete:
            entry["checked_at"] = timestamp
        local = self._local()
        local["regions"][key] = entry
        self.store.write(FILE, local)
        return {**data, **entry, "source": data["source"]}
