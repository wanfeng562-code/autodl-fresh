from autodl_fresh.models import Instance
from autodl_fresh.quote_cache import QuoteCache, bundled_quotes
from autodl_fresh.storage import Store


def source(uuid="sourceaa-12345678", region="测试地区 / A01机", **kwargs):
    return Instance(uuid=uuid, region=region, status="已关机", gpu_name="RTX 4090", gpu_count=1, **kwargs)


def catalog(item):
    return {"source": item.to_dict(), "gpu_names": ["RTX 4090", "RTX 3090"],
            "gpu_prices": {"RTX 4090": {"min": "1.98", "max": "2.08"}}}


def test_regional_quotes_survive_restart_and_are_reused_for_a_different_source(tmp_path):
    store = Store(tmp_path)
    QuoteCache(store).update(catalog(source()), complete=True)
    other = source("sourcebb-87654321", region="测试地区 / A02机")
    cache = QuoteCache(Store(tmp_path))
    cached = cache.get(other)
    assert cached["source"]["uuid"] == other.uuid and cached["gpu_prices"]["RTX 4090"]["min"] == "1.98"
    assert cached["checked_at"]
    assert cache.get(source(region="北京A区 / A01机")) is None


def test_partial_or_failed_recheck_keeps_last_price_and_does_not_extend_completed_freshness(tmp_path):
    cache = QuoteCache(Store(tmp_path))
    original = cache.update(catalog(source()), complete=True)
    data = catalog(source())
    data.update(gpu_prices={}, price_errors={"RTX 4090": "网页筛选后没有主机报价"})
    cache.update(data)
    result = cache.get(source())
    assert result["gpu_prices"] == original["gpu_prices"]
    assert result["checked_at"] == original["checked_at"]
    assert cache.get(source())["gpu_prices"]["RTX 4090"]["min"] == "1.98"


def test_bundled_quotes_are_available_immediately_in_an_empty_data_directory(tmp_path):
    cache = QuoteCache(Store(tmp_path))
    cached = cache.get(source(region="西北B区 / A01机"))
    assert len(cached["gpu_names"]) == len(cached["gpu_prices"]) == 20
    assert cached["gpu_prices"]["vGPU-48GB-350W"]["min"] == "1.87"
    assert cached["quote_origin"] == "bundled"
    assert not (tmp_path / 'gpu-quotes.json').exists()
    for entry in bundled_quotes().values():
        assert set(entry) == {'checked_at','gpu_names','gpu_prices'}


def test_corrupt_disposable_quote_cache_does_not_block_operations(tmp_path):
    (tmp_path / 'gpu-quotes.json').write_text('broken', encoding='utf-8')
    cache = QuoteCache(Store(tmp_path))
    assert cache.get(source()) is None
    cache.update(catalog(source()), complete=True)
    assert cache.get(source())["gpu_prices"]["RTX 4090"]["min"] == '1.98'


def test_manual_local_quotes_override_bundle_and_never_expire_automatically(tmp_path):
    store = Store(tmp_path)
    item = source(region='西北B区 / A01机')
    data = catalog(item)
    data['gpu_prices']['RTX 4090'] = {'min':'1.23','max':'1.23'}
    QuoteCache(store).update(data, complete=True)
    state = store.read('gpu-quotes.json')
    state['regions']['西北B区']['checked_at'] = '2000-01-01T00:00:00+08:00'
    store.write('gpu-quotes.json', state)
    cached = QuoteCache(Store(tmp_path)).get(item)
    assert cached['gpu_prices']['RTX 4090']['min'] == '1.23'
    assert cached['quote_origin'] == 'local' and cached['checked_at'].startswith('2000-')
