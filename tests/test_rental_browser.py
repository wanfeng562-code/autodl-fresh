"""Exercise real Playwright DOM actions against a fully intercepted local site."""
import json
import threading
import time
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from autodl_fresh.browser import ActionRejected, AutoDLConsole, ConsoleError, LayoutChanged
from autodl_fresh.rental import RentalEngine, RentalOptions
from autodl_fresh.rental_browser import CloneConsole
from autodl_fresh.storage import Settings, Store

SOURCE = "sourceaa-12345678"
TARGET = "targetaa-87654321"
ORIGIN = "http://127.0.0.1:18937"


class CloneSite:
    def __init__(self):
        self.orders = []
        self.target = False
        self.bad_body = False
        self.rejected = False
        self.order_replies = []
        self.hold_order = False
        self.held_orders = []
        self.creations = 0
        self.copy_status = "Succeed"
        self.clone_status = ""
        self.target_status = "starting"
        self.status_queries = []
        self.status_replies = []
        self.disposed_responses = 0
        self.price = "2.00"
        self.api_price = None
        self.disk_price = "0"
        self.expanded = "0"
        self.wrong_region = False
        self.source_region = "西北B区"
        self.route_region = "west-B"
        self.special_zone = False
        self.lazy = False
        self.sortable = False
        self.market_queries = []
        self.market_failures = 0
        self.market_auth_failed = False
        self.page_views = 0
        self.page_paths = []
        self.order_page_views = []
        self.machines = [dict(id="m1", gpu="RTX 4090", idle=2, price="2.00"),
                         dict(id="m2", gpu="RTX 3090", idle=1, price="1.00")]

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        if path == "/console/instance/list":
            self.page_views += 1
            self.page_paths.append(path)
            route.fulfill(content_type="text/html; charset=utf-8", body=self.instances())
        elif path == "/cloneInstance":
            self.page_views += 1
            self.page_paths.append(path)
            route.fulfill(content_type="text/html; charset=utf-8", body=self.clone())
        elif path == "/api/v1/instance":
            route.fulfill(json={"code": "Success", "data": {"list": [
                {"uuid": TARGET, "clone_status": "Success", "additional_info": {
                    "copy_data_disk_after_clone_status": self.copy_status}},
            ]}})
        elif path == CloneConsole.ORDER_PATH:
            self.orders.append(request.post_data_json)
            self.order_page_views.append(self.page_views)
            if self.hold_order:
                self.held_orders.append(route)
            elif self.order_replies:
                status, payload = self.order_replies.pop(0)
                route.fulfill(status=status, json=payload)
            elif self.rejected:
                route.fulfill(json={"code": "TORecharge", "msg": "余额不足"})
            else:
                self.target = True
                self.creations += 1
                route.fulfill(json={"code": "Success", "data": TARGET})
        elif path == "/api/v1/order/price/preview":
            route.fulfill(json={"code": "Success", "data": {
                "discount_list": [{"product_type": "instance", "origin_price": float(self.api_price or self.price) * 1000,
                                   "total_reduction": 0}],
                "data_disk_info": {"data_disk_deal_daily_price": float(self.disk_price) * float(self.expanded) * 1000},
            }})
        elif path == CloneConsole.MACHINE_PATH:
            if request.method == "GET":
                if self.lazy:
                    time.sleep(1.1)
                body = {}
            else:
                body = request.post_data_json
                self.market_queries.append(body)
            if self.market_auth_failed:
                route.fulfill(json={"code": "AuthorizeFailed"})
                return
            if self.market_failures:
                self.market_failures -= 1
                route.fulfill(status=503, json={"code": "Unavailable"})
                return
            rows = [m for m in self.machines if (not body.get("gpu_type_name") or m["gpu"] in body["gpu_type_name"])
                    and m["idle"] >= body.get("gpu_idle_num", 0)]
            if body.get('gpu_idle_type') == 'desc':
                rows.sort(key=lambda m:m['idle'], reverse=True)
            size, index = body.get("page_size", 10), body.get("page_index", 1)
            data = [{"machine_id":m["id"], "gpu_name":m.get("alias", m["gpu"]), "gpu_idle_num":m["idle"],
                     "region_sign":self.route_region, "on_line":2, "max_instance_num":10, "binding_instance_num":0,
                     "machine_sku_info":[{"type":"payg","current_price":float(m["price"])*1000}]} for m in rows]
            route.fulfill(json={"code": "Success", "data": {"list":data[(index-1)*size:index*size],
                          "result_total":len(data), "page_size":size, "page_index":index}})
        else:
            route.abort()

    def instances(self):
        items = [(SOURCE, "已关机", "2天后释放")]
        if self.target:
            items.append((TARGET, "运行中", "关机15天后释放"))
        rows = ''.join(f'''<tr class="el-table__row"><td><div>{self.source_region} / A01机</div><div>{uuid}</div><div>示例</div></td>
        <td>{status}</td><td>RTX 4090 * 1卡</td><td>按量计费</td><td>{release}</td>
        <td><button onclick="document.querySelector('.el-dropdown-menu').hidden=false">更多</button></td></tr>'''
                       for uuid, status, release in items)
        return f'''<meta charset="utf-8"><style>[hidden]{{display:none!important}} td{{padding:8px}}</style>
        <div class="loading-box instance-table"><div class="el-table__header-wrapper"><table><tr>
        <th>实例ID</th><th>状态</th><th>规格详情</th><th>付费方式</th><th>释放时间</th><th>操作</th></tr></table></div>
        <div class="el-table__body-wrapper"><table><tbody>{rows}</tbody></table></div>
        <div class="el-pagination">共 {len(items)} 条<button class="btn-next" disabled>下一页</button></div></div>
        <div class="el-dropdown-menu" hidden><button onclick="document.querySelector('.el-dialog').hidden=false">克隆实例</button></div>
        <div class="el-dialog" hidden>克隆实例 今天剩余克隆次数：10次
        <label><input type="checkbox" checked disabled>系统盘</label><label><input type="checkbox" id="copy">数据盘</label>
        <button onclick="location.href='/cloneInstance?id={SOURCE}&amp;region={self.route_region}&amp;copy_data_disk='+ (document.querySelector('#copy').checked?'1':'')+'&amp;expand_size=0'">继续</button></div>
        <script>fetch('/api/v1/instance',{{method:'POST'}})</script>'''

    def clone(self):
        machines = json.dumps(self.machines)
        region = "北京A区" if self.wrong_region else "西北B区"
        second = ('<div class="filter-item second-region"><label><input type="radio" value="beijingDC3" checked>V100专区</label></div>'
                  if self.special_zone else '')
        radios = ''.join(f'<label><span><input type="checkbox" value="{gpu}" onchange="filtersChanged(this)"></span>{gpu}</label>'
                         for gpu in ("RTX 4090", "RTX 3090"))
        sort_button = '<i class="sort-caret descending" style="display:inline-block;width:20px;height:20px" onclick="sortChanged()">↓</i>' if self.sortable else ''
        return f'''<meta charset="utf-8"><style>td{{padding:12px}} .el-table__body-wrapper{{height:160px;overflow:auto}}</style>
        <div class="create-wrap"><div>{SOURCE} / 示例</div><label><input type="radio" value="payg" checked>按量计费</label>
        <div class="select-server"><div class="list-filter">
        <div class="filter-item">选择地区:<label><input type="radio" value="westDC3" {'' if self.special_zone else 'checked'}>{region}</label></div>{second}
        <div class="filter-item" id="gpu-types">GPU型号:<div class="st-checkbox">{radios}</div></div>
        <div class="filter-item">GPU数量:<label><input type="radio" value="1" checked onchange="filtersChanged()">1</label><label><input type="radio" value="2" onchange="filtersChanged()">2</label></div>
        </div></div><div class="machine-table"><div class="el-table__header-wrapper"><table><tr>
        <th></th><th>主机ID</th><th>算力型号/显存</th><th id="idle-header">空闲GPU{sort_button}</th><th>价格(单卡)</th></tr></table></div>
        <div class="el-table__body-wrapper"><table><tbody id="machines"></tbody></table></div></div>
        <div class="data-disk"><input type="text" value="{self.expanded}"><span>{self.disk_price}元/日/GB</span></div>
        <div class="pay-wrap"><div class="sum">配置费用：￥<span id="quote">{self.price}</span>/时</div>
        <button onclick="create()">创建并开机</button></div></div>
        <script>let selected='', machines={machines}, loaded={10 if self.lazy else 1000}, loading=false,
          total=machines.length, pageIndex=1, idleSort='';window.toggles=[];
        function allowedModels(){{return [...document.querySelectorAll('#gpu-types input[type=checkbox]:checked')].map(e=>e.value)}}
        function render(){{let allowed=allowedModels();
          document.querySelector('#machines').innerHTML=machines.filter(m=>m.fromServer||!allowed.length||allowed.includes(m.gpu)).slice(0,loaded).map(m=>
          `<tr class="el-table__row" onclick="choose('${{m.id}}')"><td><label><input type="radio" name="machine" value="${{m.id}}" ${{m.id===selected?'checked':''}} ${{m.idle?'':'disabled'}}></label></td>
          <td><span class="machineId">${{m.id}}</span></td><td>${{m.gpu}}<br>24GB</td><td>${{m.idle}} / 8</td><td>￥${{m.price}}/时</td></tr>`).join('');}}
        function normalize(rows){{return rows.map(m=>({{id:m.machine_id,gpu:m.gpu_name,idle:m.gpu_idle_num,fromServer:true,
          price:String(m.machine_sku_info[0].current_price/1000)}}))}}
        async function filtersChanged(control){{
          if(control)window.toggles.push({{name:control.value,checked:control.checked,all:allowedModels()}});
          const r=await fetch('{CloneConsole.MACHINE_PATH}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{gpu_type_name:allowedModels(),gpu_idle_num:0,page_index:1,page_size:10,gpu_idle_type:idleSort}})}});
          const p=await r.json();if(p.code!=='Success')return;
          machines=normalize(p.data.list);total=p.data.result_total;pageIndex=1;loaded=1000;render();
        }}
        function sortChanged(){{idleSort='desc';document.querySelector('#idle-header').className='descending';filtersChanged();}}
        function choose(id){{selected=id;render();fetch('/api/v1/order/price/preview',{{method:'POST',
          body:JSON.stringify({{machine_id:id,num:1,expand_data_disk:Number('{self.expanded}')*1024**3,charge_type:'payg'}})}})
          .then(r=>r.json()).then(()=>document.querySelector('#quote').textContent='{self.price}');}}
        async function create(){{let body={{instance_uuid:'{SOURCE}',instance_info:{{machine_id:selected,charge_type:'payg',req_gpu_amount:1,
          copy_data_disk_after_clone:{str(not self.bad_body).lower()},expand_data_disk:0}},price_info:{{machine_id:selected,charge_type:'payg',num:1,expand_data_disk:0}}}};
          let r=await fetch('{CloneConsole.ORDER_PATH}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
          let p=await r.json();if(p.code==='Success')setTimeout(()=>location.href='/console/instance/list',1000);}}
        document.querySelector('.machine-table .el-table__body-wrapper').addEventListener('scroll',async()=>{{
          if(pageIndex*10>=total||loading)return;loading=true;
          const r=await fetch('{CloneConsole.MACHINE_PATH}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{gpu_type_name:allowedModels(),gpu_idle_num:0,page_index:pageIndex+1,page_size:10,gpu_idle_type:idleSort}})}});
          const p=await r.json();if(p.code==='Success'){{machines=machines.concat(normalize(p.data.list));pageIndex++;loaded=1000;render();}}
          loading=false;
        }});
        render();</script>'''


class TargetClient:
    def __init__(self, site):
        self.site = site

    def post(self, url, **kwargs):
        site = self.site
        assert url == ORIGIN + '/api/v1/instance'
        assert kwargs['data'] == {'name':TARGET,'page_index':1,'page_size':1}
        assert kwargs['max_redirects'] == 0
        site.status_queries.append(kwargs)
        rows = []
        if site.target:
            rows = [{'uuid':TARGET,'status':site.target_status,'clone_status':site.clone_status,
                     'root_password':'fixture-secret-do-not-persist',
                     'additional_info':{'copy_data_disk_after_clone_status':site.copy_status}}]
        status, payload = (site.status_replies.pop(0) if site.status_replies else
                           (200, {'code':'Success','data':{'list':rows,'result_total':len(rows)}}))
        class Response:
            def json(self):
                return payload
            def dispose(self):
                site.disposed_responses += 1
        response = Response()
        response.status = status
        return response


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def site_adapter(browser):
    site = CloneSite()
    context = browser.new_context()
    context.route("**/*", site.route)
    page = context.new_page()
    console = AutoDLConsole(page, Settings(console_url=ORIGIN + "/console/instance/list", timeout_seconds=3,
                                           poll_seconds=0.1))
    adapter = CloneConsole(console, request_client=TargetClient(site))
    yield site, adapter
    context.close()


def config(**kw):
    return RentalOptions(source_uuid=SOURCE, gpu_names=["RTX 4090"], **kw)


def test_dom_flow_copies_both_disks_filters_exact_gpu_and_verifies_boot(site_adapter, tmp_path):
    site, adapter = site_adapter
    task = RentalEngine(adapter, Store(tmp_path), config()).run()
    assert task["phase"] == "completed", task
    assert len(site.orders) == 1 and site.orders[0]["instance_info"]["copy_data_disk_after_clone"] is True
    assert site.orders[0]["instance_info"]["machine_id"] == "m1"
    assert task["target_uuid"] == TARGET


@pytest.mark.parametrize('copy_status', ['Succeed','Success'])
def test_created_notice_precedes_single_target_wait_and_never_scans_pages(site_adapter, tmp_path, copy_status):
    site, adapter = site_adapter
    site.copy_status = copy_status
    adapter.console.scan = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Must not scan any pages'))
    accepted = []
    def changed(task):
        if task['phase'] == 'accepted':
            assert not site.status_queries
            accepted.append(task['target_uuid'])
    store = Store(tmp_path)
    task = RentalEngine(adapter, store, config(), changed=changed).run()
    assert task['phase'] == 'completed' and accepted == [TARGET]
    assert site.status_queries and site.disposed_responses == len(site.status_queries)
    assert site.page_paths == ['/console/instance/list','/cloneInstance']
    assert adapter.page.url == 'about:blank#autodl-fresh-clone-wait'
    assert '正在等待' in adapter.page.locator('#clone-progress').inner_text()
    assert 'fixture-secret' not in json.dumps(store.read('rental-task.json'))


def test_wait_retries_temporary_status_errors_then_finishes_only_the_created_target(site_adapter, tmp_path):
    site, adapter = site_adapter
    adapter.console.settings.poll_seconds = .05
    site.status_replies = [(503, {'code':'Unavailable'}),
                           (200, {'code':'Success','data':{'list':[]}}),
                           (200, {'code':'Success','data':{'list':[{'uuid':TARGET,'clone_status':'Queued',
                                   'additional_info':{'copy_data_disk_after_clone_status':'Running'}}]}})]
    task = RentalEngine(adapter, Store(tmp_path), config()).run()
    assert task['phase'] == 'completed' and len(site.orders) == 1 and len(site.status_queries) == 4
    assert site.page_paths == ['/console/instance/list','/cloneInstance']


@pytest.mark.parametrize('reply,expected', [
    ((200,{'code':'Success','data':{'list':[{'uuid':TARGET,'additional_info':{
        'copy_data_disk_after_clone_status':'Failed'}}]}}),'拷贝失败'),
    ((200,{'code':'Success','data':{'list':[{'uuid':'different-12345678'}]}}),'不匹配'),
    ((401,{'code':'AuthorizeFailed'}),'登录'),
])
def test_status_failure_preserves_new_target_without_scanning_or_reordering(site_adapter, tmp_path, reply, expected):
    site, adapter = site_adapter
    site.status_replies = [reply]
    store = Store(tmp_path)
    task = RentalEngine(adapter, store, config()).run()
    assert task['phase'] == 'uncertain' and task['target_uuid'] == TARGET and len(site.orders) == 1
    assert expected in task['message'] and len(site.status_queries) == 1
    with pytest.raises(ConsoleError, match='尚未确认'):
        RentalEngine(adapter, store, config()).run()


def test_recovery_queries_known_target_without_loading_source_or_instance_pages(site_adapter, tmp_path):
    site, adapter = site_adapter
    site.target = True
    store = Store(tmp_path)
    store.write('rental-task.json', {'phase':'accepted','target_uuid':TARGET,
                'options':{'source_uuid':SOURCE,'gpu_names':['RTX 4090']}})
    adapter.console.scan = adapter.console.get = lambda *args: (_ for _ in ()).throw(AssertionError('No instance UI access'))
    task = RentalEngine(adapter, store, config()).run(recover=True)
    assert task['phase'] == 'completed' and not site.orders and not site.page_paths
    assert len(site.status_queries) == 1


def test_released_target_missing_stops_after_three_reads_and_can_be_manually_resolved(site_adapter, tmp_path):
    from autodl_fresh.rental import confirm_rental_result
    site, adapter = site_adapter
    adapter.console.settings.poll_seconds = .02
    store = Store(tmp_path)
    old = {'phase':'accepted','target_uuid':TARGET,
           'options':{'source_uuid':SOURCE,'gpu_names':['RTX 4090']}}
    store.write('rental-task.json', old)
    with pytest.raises(ConsoleError, match='连续 3 次未找到'):
        RentalEngine(adapter, store, config()).run(recover=True)
    assert len(site.status_queries) == 3 and not site.orders and not site.page_paths
    result = confirm_rental_result(store, 'released', expected=old)
    assert result['phase'] == 'resolved' and result['manual_result'] == 'released'


@pytest.mark.parametrize('status', ['removing','removed'])
def test_explicitly_released_target_never_waits_for_copy(site_adapter, tmp_path, status):
    site, adapter = site_adapter
    site.target, site.target_status, site.copy_status = True, status, 'Running'
    store = Store(tmp_path)
    store.write('rental-task.json', {'phase':'accepted','target_uuid':TARGET,
                'options':{'source_uuid':SOURCE,'gpu_names':['RTX 4090']}})
    with pytest.raises(ConsoleError, match='请点击“确认实例已释放”'):
        RentalEngine(adapter, store, config()).run(recover=True)
    assert len(site.status_queries) == 1 and not site.orders and not site.page_paths


def test_manual_confirmation_interrupts_a_long_poll_without_another_status_request(site_adapter, tmp_path):
    from autodl_fresh.rental import RentalConfirmation
    site, adapter = site_adapter
    site.target, site.copy_status = True, 'Running'
    adapter.console.settings.poll_seconds = 30
    store, confirmation = Store(tmp_path), RentalConfirmation()
    store.write('rental-task.json', {'phase':'accepted','target_uuid':TARGET,
                'options':{'source_uuid':SOURCE,'gpu_names':['RTX 4090']}})
    post = adapter.request_client.post
    def confirm_after_read(*args, **kwargs):
        response = post(*args, **kwargs)
        confirmation.request('completed', store.read('rental-task.json'))
        return response
    adapter.request_client.post = confirm_after_read
    result = RentalEngine(adapter, store, config(), confirmation=confirmation).run(recover=True)
    assert result['phase'] == 'completed' and result['verification'] == 'manual'
    assert len(site.status_queries) == 1 and not site.orders and not site.page_paths


def test_dom_source_region_mismatch_fails_before_any_order(site_adapter):
    site, adapter = site_adapter
    site.wrong_region = True
    with pytest.raises(LayoutChanged, match="地区"):
        adapter.prepare(SOURCE)
    assert site.orders == []


def test_region_route_alias_and_inventory_changes_are_not_region_switches(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    assert adapter.region_sign == "west-B" and adapter.region_value == "westDC3"
    site.machines[0]["idle"] = 0
    assert not adapter.offers(config(), threading.Event())
    site.machines[0]["idle"] = 1
    assert adapter.offers(config(), threading.Event())[0].idle == 1
    adapter.page.locator('input[type="radio"][value="westDC3"]').evaluate("e=>e.value='beijingDC2'")
    with pytest.raises(LayoutChanged, match="地区已切换"):
        adapter.catalog()
    assert not site.orders


def test_special_zone_on_second_region_row_can_be_selected(site_adapter):
    site, adapter = site_adapter
    site.special_zone = True
    site.source_region = "V100专区"
    site.route_region = "v100"
    adapter.prepare(SOURCE)
    assert adapter.region_value == "beijingDC3"
    assert adapter.catalog()["gpu_names"]
    assert not site.orders


def test_gpu_catalog_waits_for_async_options_and_ignores_other_filter_checkboxes(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    gpu_field = adapter.page.locator(adapter.FILTER).locator(".filter-item").filter(has_text="GPU型号")
    gpu_field.evaluate("""e=>{
        const original=e.innerHTML;
        e.innerHTML='GPU型号：加载中';
        setTimeout(()=>e.innerHTML=original,700);
    }""")
    adapter.page.locator(adapter.FILTER).evaluate("""e=>{
        const other=document.createElement('div'); other.className='filter-item';
        other.innerHTML='CPU 架构<div class="st-checkbox"><input type="checkbox" value="RTX 4090"></div>';
        e.appendChild(other);
    }""")
    assert adapter.catalog()["gpu_names"] == ["RTX 4090", "RTX 3090"]
    assert not site.orders


@pytest.mark.parametrize("kind", ["quote", "machine"])
def test_passive_observers_tolerate_page_closing_during_response_body(site_adapter, kind):
    _, adapter = site_adapter
    from types import SimpleNamespace
    class ClosingResponse:
        status = 200
        request = SimpleNamespace(post_data_json={})
        url = ORIGIN + {"quote": "/api/v1/order/price/preview",
                        "machine": CloneConsole.MACHINE_PATH}[kind]
        def json(self):
            raise PlaywrightError("Target page, context or browser has been closed")
    observer = {"quote": adapter._observe_quote,
                "machine": adapter._machine_response}[kind]
    observer(ClosingResponse())
    assert adapter.price_key is None


@pytest.mark.parametrize("kind", ["hourly", "disk", "capacity"])
def test_final_quote_is_read_without_caps_but_disk_capacity_is_enforced(site_adapter, kind):
    site, adapter = site_adapter
    if kind == "hourly":
        site.price = "4.00"
    else:
        site.expanded = "100"
        site.disk_price = "0.1"
    adapter.prepare(SOURCE)
    offer = adapter.offers(config(), threading.Event())[0]
    if kind == "capacity":
        adapter.expand_bytes = 200 * 1024**3
        with pytest.raises(LayoutChanged, match="容量"):
            adapter.select(offer, config())
    else:
        quote = adapter.select(offer, config())
        assert quote is not None
        assert float(quote["hourly"]) == 4 if kind == "hourly" else float(quote["disk_daily"]) == 10
    assert site.orders == []


def test_dom_request_guard_prevents_missing_data_disk_and_wrong_card_count(site_adapter):
    site, adapter = site_adapter
    site.bad_body = True
    adapter.prepare(SOURCE)
    offer = adapter.offers(config(), threading.Event())[0]
    assert adapter.select(offer, config())
    adapter.RESPONSE_TIMEOUT = 700
    with pytest.raises(ActionRejected):
        adapter.create(offer, config(), lambda: None)
    assert not site.orders


def test_delayed_website_preflight_cannot_create_after_timeout(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    offer = adapter.offers(config(), threading.Event())[0]
    assert adapter.select(offer, config())
    adapter.page.evaluate("window.originalCreate = create; window.create = () => setTimeout(originalCreate, 700); void 0")
    adapter.RESPONSE_TIMEOUT = 200
    with pytest.raises(ActionRejected):
        adapter.create(offer, config(), lambda: None)
    adapter.page.wait_for_timeout(1000)
    assert not site.orders


def test_exact_platform_price_is_recorded_without_a_price_cap(site_adapter):
    site, adapter = site_adapter
    site.price = "2.00"
    site.api_price = "2.001"
    adapter.prepare(SOURCE)
    options = config()
    offer = adapter.offers(options, threading.Event())[0]
    quote = adapter.select(offer, options)
    assert float(quote["hourly"]) == 2.001
    assert not site.orders


def test_dom_copy_still_running_keeps_pending_target(site_adapter, tmp_path):
    site, adapter = site_adapter
    site.copy_status = "Running"
    adapter.console.settings.poll_seconds = 0.1
    notifications = []
    task = RentalEngine(adapter, Store(tmp_path), config(), changed=lambda t:notifications.append(dict(t))).run()
    assert task["phase"] == "uncertain" and task["target_uuid"] == TARGET
    assert len(site.orders) == 1
    accepted = [t for t in notifications if t['phase']=='accepted']
    assert len(accepted) == 1 and accepted[0]['target_uuid'] == TARGET
    assert site.status_queries and site.disposed_responses == len(site.status_queries)


def test_dom_explicit_platform_rejection_is_not_success(site_adapter, tmp_path):
    site, adapter = site_adapter
    site.rejected = True
    task = RentalEngine(adapter, Store(tmp_path), config()).run()
    assert task["phase"] == "failed" and len(site.orders) == 1


@pytest.mark.parametrize('payload', [
    {'code':'MachineGpuNumUpdateFailed','msg':'该主机空闲GPU数量不足，请更换主机。'},
    {'code':'NoStock'},
    {'code':'Failed','msg':'该主机空闲 GPU 数量不足，请更换主机。'},
])
def test_lost_stock_retries_on_same_clone_document_and_only_creates_one_target(site_adapter, tmp_path, payload):
    site, adapter = site_adapter
    site.order_replies = [(200, payload)]
    changes = []
    task = RentalEngine(adapter, Store(tmp_path), config(),
                        changed=lambda t:changes.append(dict(t)), wait=lambda _:None).run()
    assert task['phase'] == 'completed' and task['stock_rejections'] == 1
    assert len(site.orders) == 2 and site.creations == 1
    assert task['target_uuid'] == TARGET
    assert site.page_paths.count('/cloneInstance') == 1
    assert site.order_page_views[0] == site.order_page_views[1]
    assert any(t['phase']=='monitoring' and t.get('last_rejection_code')==payload['code'] for t in changes)
    assert adapter.page.url == 'about:blank#autodl-fresh-clone-wait'
    assert site.page_paths == ['/console/instance/list','/cloneInstance']


def test_late_website_request_is_blocked_between_definite_stock_rejection_and_retry(site_adapter, tmp_path):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    site.order_replies = [(200, {'code':'MachineGpuNumUpdateFailed'})]
    adapter.page.evaluate("""window.originalCreate=create;window.lateScheduled=false;
        window.create=async()=>{await originalCreate();if(!window.lateScheduled){window.lateScheduled=true;
          setTimeout(()=>originalCreate().catch(()=>{}),100)}};void 0""")
    waits = []
    def wait(seconds):
        adapter.page.wait_for_timeout(300)
        assert len(site.orders) == 1  # The late replay never reaches the server.
        waits.append(seconds)
    task = RentalEngine(adapter, Store(tmp_path), config(), wait=wait).run()
    assert task['phase'] == 'completed' and waits == [1]
    assert len(site.orders) == 2 and site.creations == 1


def test_response_loss_on_retry_preserves_uncertainty_and_does_not_try_a_third_order(site_adapter, tmp_path):
    site, adapter = site_adapter
    site.order_replies = [(200, {'code':'NoStock'}), (503, {'code':'Failed'})]
    task = RentalEngine(adapter, Store(tmp_path), config(), wait=lambda _:None).run()
    assert task['phase'] == 'uncertain' and task['stock_rejections'] == 1
    assert len(site.orders) == 2


@pytest.mark.parametrize('code,message', [
    ('TORecharge','余额不足'), ('TORealName','请完成实名认证'),
    ('PermissionDenied','没有权限'), ('CloneCountLimit','今日克隆次数不足'),
    ('UnknownFailure','未知错误'), ('TORecharge','空闲GPU数量不足，余额不足'),
])
def test_permanent_rejections_are_not_misclassified_as_stock_loss(site_adapter, tmp_path, code, message):
    site, adapter = site_adapter
    site.order_replies = [(200, {'code':code,'msg':message})]
    task = RentalEngine(adapter, Store(tmp_path), config(), wait=lambda _:None).run()
    assert task['phase'] == 'failed' and len(site.orders) == 1 and not site.creations
    assert code in task['message'] and task['stock_rejections'] == 0


@pytest.mark.parametrize('status,payload', [
    (503, {'code':'MachineGpuNumUpdateFailed'}),
    (200, {'code':'MachineGpuNumUpdateFailed','data':TARGET}),
    (200, {'code':'NoStock','data':{'uuid':TARGET}}),
    (200, {'msg':'空闲GPU数量不足'}),
])
def test_unconfirmed_responses_never_retry_even_with_stock_words(site_adapter, tmp_path, status, payload):
    site, adapter = site_adapter
    site.order_replies = [(status, payload)]
    store = Store(tmp_path)
    task = RentalEngine(adapter, store, config(), wait=lambda _:None).run()
    assert task['phase'] == 'uncertain' and len(site.orders) == 1
    with pytest.raises(ConsoleError, match='尚未确认'):
        RentalEngine(adapter, store, config()).run()


def test_a_blocked_duplicate_does_not_erase_uncertainty_of_a_forwarded_order(site_adapter, tmp_path):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    site.hold_order = True
    adapter.RESPONSE_TIMEOUT = 350
    adapter.page.evaluate("""window.originalCreate=create;
        window.create=()=>{originalCreate().catch(()=>{});setTimeout(()=>originalCreate().catch(()=>{}),20)};void 0""")
    store = Store(tmp_path)
    task = RentalEngine(adapter, store, config()).run()
    assert task['phase'] == 'uncertain' and len(site.orders) == 1 and len(site.held_orders) == 1
    with pytest.raises(ConsoleError, match='尚未确认'):
        RentalEngine(adapter, store, config()).run()


def test_dom_only_enabled_offers_and_cancellation(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    assert adapter.source.gpu_name == "RTX 4090" and adapter.source.gpu_count == 1
    assert adapter.catalog()["gpu_names"] == ["RTX 4090", "RTX 3090"]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConsoleError, match="停止"):
        adapter.offers(config(), cancel)
    assert not site.orders


def test_slow_scroll_loading_finds_stock_after_initial_ten_rows(site_adapter):
    site, adapter = site_adapter
    site.lazy = True
    site.machines = [dict(id=f"m{i}", gpu="RTX 4090", idle=0, price="2") for i in range(10)]
    site.machines.append(dict(id="available-last", gpu="RTX 4090", idle=2, price="2"))
    adapter.prepare(SOURCE)
    offers = adapter.offers(config(), threading.Event())
    assert [o.machine_id for o in offers] == ["available-last"]
    assert not site.orders


def test_catalog_prices_include_busy_hosts_and_keep_gpu_names_separate(site_adapter):
    site, adapter = site_adapter
    site.machines[0]["idle"] = 0
    adapter.prepare(SOURCE)
    catalog = adapter.catalog(include_prices=True, cancel=threading.Event())
    assert catalog["gpu_prices"] == {"RTX 4090": {"min": "2", "max": "2"},
                                      "RTX 3090": {"min": "1", "max": "1"}}
    assert catalog["gpu_names"] == ["RTX 4090", "RTX 3090"] and not site.orders


def test_checkbox_polling_keeps_same_page_and_reads_lazy_rows(site_adapter):
    site, adapter = site_adapter
    site.machines = [dict(id=f"m{i}", gpu="RTX 4090", idle=1, price="2") for i in range(95)]
    adapter.prepare(SOURCE)
    views, url = site.page_views, adapter.page.url
    options = config()
    options.gpu_names = ["RTX 3090", "RTX 4090"]
    offers = adapter.offers(options, threading.Event())
    assert len(offers) == 95 and max(q["page_index"] for q in site.market_queries) == 10
    assert site.page_views == views and adapter.page.url == url and not site.orders


def test_temporary_stock_failure_retries_without_second_order(site_adapter, tmp_path):
    site, adapter = site_adapter
    site.market_failures = 1
    changes = []
    task = RentalEngine(adapter, Store(tmp_path), config(), changed=lambda task: changes.append(dict(task))).run()
    assert task["phase"] == "completed", task
    assert any("自动重试" in task["message"] for task in changes)
    assert len(site.orders) == 1


def test_inventory_auth_expiry_stops_instead_of_retrying_forever(site_adapter):
    from autodl_fresh.browser import SessionExpired
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    site.market_auth_failed = True
    with pytest.raises(SessionExpired):
        adapter.offers(config(), threading.Event())
    assert site.market_queries and not site.orders


def test_each_poll_toggles_only_last_selected_model_and_never_navigates_before_order(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    options = config()
    options.gpu_names = ["RTX 4090", "RTX 3090"]
    views = site.page_views
    adapter.offers(options, threading.Event())
    before = adapter.page.evaluate('window.toggles.length')
    offers = adapter.offers(options, threading.Event())
    toggles = adapter.page.evaluate('window.toggles').__getitem__(slice(before, None))
    assert [(e['name'], e['checked']) for e in toggles] == [('RTX 3090', False), ('RTX 3090', True)]
    assert set(toggles[-1]['all']) == set(options.gpu_names)
    assert site.page_views == views
    chosen = next(item for item in offers if item.gpu_name == 'RTX 4090')
    assert adapter.select(chosen, options)
    assert site.page_views == views  # Select on the very same clone document.
    assert adapter.create(chosen, options, lambda: None) == TARGET
    assert len(site.orders) == 1


def test_failed_initial_table_can_retry_inside_existing_clone_page(site_adapter):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    views = site.page_views
    adapter.machine_failed = True
    adapter.prepare(SOURCE)
    assert adapter.offers(config(), threading.Event())
    assert site.page_views == views and not site.orders


def test_individual_web_filter_can_quote_a_busy_host_with_a_different_display_alias(site_adapter):
    site, adapter = site_adapter
    site.machines[0].update(alias='NVIDIA RTX 4090', idle=0)
    adapter.prepare(SOURCE)
    catalog = adapter.catalog(include_prices=True, cancel=threading.Event())
    assert catalog['gpu_prices']['RTX 4090']['min'] == '2'
    assert catalog['gpu_prices']['RTX 3090']['min'] == '1'
    events = adapter.page.evaluate('window.toggles')
    assert any(e['all'] == ['RTX 4090'] for e in events)
    assert any(e['all'] == ['RTX 3090'] for e in events)
    assert not site.orders


def test_old_cancelled_and_failed_requests_cannot_poison_new_table(site_adapter):
    _, adapter = site_adapter
    adapter.prepare(SOURCE)
    class Request:
        url = ORIGIN + CloneConsole.MACHINE_PATH
        failure = "net::ERR_CONNECTION_RESET"
        headers = {}
    class Response:
        status = 200
        def __init__(self, request):
            self.request = request
            self.url = request.url
        def json(self):
            return {"code": "Success"}
    old, fresh = Request(), Request()
    adapter._machine_request(old)
    adapter._machine_failed(old)
    assert adapter.machine_failed
    adapter._machine_request(fresh)
    adapter._machine_response(Response(fresh))
    adapter.machine_pending.discard(fresh)
    adapter._machine_failed(old)
    assert not adapter.machine_failed
    adapter._wait_table()
    pending_old = Request()
    adapter._machine_request(pending_old)
    adapter._new_table_round()
    adapter._machine_failed(pending_old)
    assert not adapter.machine_failed and not adapter.machine_pending


def test_foreground_rental_banner_is_nonmodal_and_background_read_does_not_show_it(site_adapter):
    _, adapter = site_adapter
    adapter.prepare(SOURCE)
    adapter.show_status({"message": "第 2 次检查，暂无空闲 GPU"})
    assert not adapter.page.locator('#autodl-fresh-rental-status').count()
    adapter.console.settings.browser_visibility = "foreground"
    adapter.show_status({"message": "第 2 次检查，暂无空闲 GPU"})
    banner = adapter.page.locator('#autodl-fresh-rental-status')
    assert "第 2 次检查" in banner.inner_text()
    assert banner.get_attribute('role') == 'status' and banner.locator('button').count() == 0


def test_overlapping_loading_masks_are_waited_out_without_strict_locator_failure(site_adapter):
    _, adapter = site_adapter
    adapter.prepare(SOURCE)
    adapter.page.locator(adapter.TABLE).evaluate("""e=>{
      e.insertAdjacentHTML('beforeend','<div class="el-loading-mask">old</div><div class="el-loading-mask">new</div>');
      setTimeout(()=>e.querySelectorAll('.el-loading-mask').forEach(n=>n.remove()),500);
    }""")
    adapter._wait_table()
    assert adapter.page.locator(adapter.TABLE).locator('.el-loading-mask').count() == 0


def test_idle_sort_finishes_empty_stock_check_without_reading_busy_host_pages(site_adapter):
    site, adapter = site_adapter
    site.sortable = True
    site.machines = [dict(id=f'busy-{i}',gpu='RTX 4090',idle=0,price='2') for i in range(95)]
    adapter.prepare(SOURCE)
    views = site.page_views
    assert adapter.offers(config(), threading.Event()) == []
    assert site.market_queries[-1]['gpu_idle_type'] == 'desc'
    assert max(q['page_index'] for q in site.market_queries) == 1
    assert site.page_views == views


def test_replacing_host_rows_while_reading_produces_a_consistent_snapshot(site_adapter):
    site, adapter = site_adapter
    site.machines = [dict(id=f'host-{i}', gpu='RTX 4090', idle=1, price='2') for i in range(100)]
    adapter.prepare(SOURCE)
    adapter.page.set_default_timeout(500)
    adapter.page.evaluate("window.churn=setInterval(()=>{loaded=loaded===1?1000:1;render()},5)")
    try:
        for _ in range(4):
            offers = adapter._read_offers()
            assert len(offers) in {1, 100}
            assert len({o.machine_id for o in offers}) == len(offers)
    finally:
        adapter.page.evaluate('clearInterval(window.churn);loaded=1000;render()')
    row = adapter._machine_row('host-95')
    adapter.page.evaluate('machines.reverse();render()')
    assert row.locator('input[type="radio"]').input_value() == 'host-95'
    assert not site.orders


def test_closed_work_tab_records_browser_connection_state_and_never_submits(site_adapter, tmp_path):
    site, adapter = site_adapter
    adapter.prepare(SOURCE)
    adapter.page.close()
    task = RentalEngine(adapter, Store(tmp_path), config()).run()
    assert task['phase'] == 'failed' and not site.orders
    assert task['browser_state'] == {'page_closed':True,'page_crashed':False,'browser_connected':True}
    assert '工作标签已关闭，浏览器仍连接' in task['message']
