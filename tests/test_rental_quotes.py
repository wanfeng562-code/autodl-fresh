from decimal import Decimal
from types import SimpleNamespace

import pytest

from autodl_fresh.rental_browser import CloneConsole
from autodl_fresh.storage import Settings


class Request:
    def __init__(self, machine, *, origin='https://www.autodl.com'):
        self.url = origin + CloneConsole.PRICE_PATH
        self.headers = {}
        self.failure = 'net::ERR_ABORTED'
        self.post_data_json = {'machine_id':machine, 'num':1, 'expand_data_disk':0, 'charge_type':'payg'}


class Response:
    def __init__(self, request, *, status=200, payload=None, before_read=None):
        self.url, self.request, self.status = request.url, request, status
        self.payload = payload if payload is not None else {'code':'Success', 'data':{
            'discount_list':[{'product_type':'instance','origin_price':2000,'total_reduction':0}]}}
        self.before_read = before_read
        self.reads = 0

    def json(self):
        self.reads += 1
        if self.before_read:
            self.before_read()
        return self.payload


def adapter():
    page = SimpleNamespace(context=SimpleNamespace(request=object()), on=lambda *args:None)
    return CloneConsole(SimpleNamespace(page=page, settings=Settings()))


@pytest.mark.parametrize('old_response', ['success','failure','network_failure'])
def test_late_old_quote_cannot_replace_or_clear_the_latest_matching_quote(old_response):
    console = adapter()
    old, new = Request('old'), Request('new')
    console._machine_request(old)
    console._machine_request(new)
    console._observe_quote(Response(new))
    if old_response == 'network_failure':
        console._machine_failed(old)
    else:
        late = Response(old, status=503 if old_response == 'failure' else 200)
        console._observe_quote(late)
        assert not late.reads
    assert console.price_key == ('new',1,0,'payg')
    assert console.price_hourly == Decimal(2) and not console.price_failed


def test_new_preview_during_old_response_read_preserves_the_new_result():
    console = adapter()
    old, new = Request('old'), Request('new')
    console._machine_request(old)
    def newer():
        console._machine_request(new)
        console._observe_quote(Response(new))
    console._observe_quote(Response(old, before_read=newer))
    assert console.price_key == ('new',1,0,'payg')


@pytest.mark.parametrize('failure', ['http','schema','network'])
def test_latest_preview_failure_invalidates_previous_cost(failure):
    console = adapter()
    old, new = Request('old'), Request('new')
    console._machine_request(old)
    console._observe_quote(Response(old))
    console._machine_request(new)
    assert console.price_key is None and console.price_hourly is None
    if failure == 'network':
        console._machine_failed(new)
    else:
        console._observe_quote(Response(new, status=503 if failure == 'http' else 200,
                                        payload={'code':'Success','data':{}}))
    assert console.price_failed and console.price_key is None


def test_other_origin_preview_does_not_invalidate_the_active_quote():
    console = adapter()
    current, foreign = Request('current'), Request('foreign',origin='https://example.invalid')
    console._machine_request(current)
    console._observe_quote(Response(current))
    console._machine_request(foreign)
    console._observe_quote(Response(foreign))
    assert console.price_key == ('current',1,0,'payg')
