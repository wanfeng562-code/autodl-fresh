"""Run the real Playwright adapter against intercepted AutoDL-shaped pages.

No test sends any network requests to AutoDL or uses the user's browser profile.
"""
import html
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from autodl_fresh.browser import AutoDLConsole, SessionExpired
from autodl_fresh.engine import RefreshEngine
from autodl_fresh.storage import Settings, Store


class FakeSite:
    def __init__(self):
        self.items = [
            dict(uuid="abcdefgh-12345678", name="待刷新 A", status="已关机", release="2天04小时34分后释放", no_gpu=False),
            dict(uuid="ijklmnop-23456789", name="时间充足", status="已关机", release="13天09小时40分后释放", no_gpu=False),
            dict(uuid="qrstuvwx-34567890", name="正在训练", status="运行中", release="关机15天后释放", no_gpu=False),
            dict(uuid="yzabcdef-45678901", name="第二页待刷新", status="已关机", release="1天01小时01分后释放", no_gpu=False),
        ]
        self.events = []
        self.broken_total = False
        self.broken_columns = False
        self.no_reset = False
        self.login_redirect = False
        self.rejected = False
        self.reorder_on_start = False

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.path in {"/api/v1/instance/power_on", "/api/v1/instance/power_off"}:
            body = request.post_data_json
            item = next(item for item in self.items if item["uuid"] == body["instance_uuid"])
            if self.rejected:
                route.fulfill(json={"code": "WrongStartMode", "msg": "unavailable"})
                return
            if parsed.path.endswith("power_on"):
                assert body["payload"] == "non_gpu"
                assert not any(i["no_gpu"] and i["status"] == "运行中" for i in self.items)
                self.events.append(("start", item["uuid"]))
                item.update(status="运行中", no_gpu=True, release="关机15天后释放")
                if self.reorder_on_start:
                    self.items.remove(item)
                    self.items.append(item)
            else:
                assert item["no_gpu"]
                self.events.append(("stop", item["uuid"]))
                item.update(status="已关机", no_gpu=False, release="2天04小时34分后释放" if self.no_reset else "14天23小时59分后释放")
            route.fulfill(json={"code": "Success", "data": None})
            return
        if parsed.path == "/login":
            route.fulfill(content_type="text/html", body="<h1>登录</h1>")
            return
        if parsed.path == "/console/instance/list":
            if self.login_redirect:
                route.fulfill(status=302, headers={"Location": "http://127.0.0.1:18937/login"})
                return
            page = int(parse_qs(parsed.query).get("page_index", ["1"])[0])
            route.fulfill(content_type="text/html; charset=utf-8", body=self.render(page))
            return
        route.abort()

    def render(self, page):
        rows = []
        for item in self.items[(page-1)*2:page*2]:
            uuid = item["uuid"]
            status = item["status"] + ("<br>无卡模式" if item["no_gpu"] else "")
            rows.append(f'''<tr class="el-table__row"><td><div>西北B区 / C46机</div><div>{uuid}</div><div><span>{html.escape(item['name'])}</span></div></td>
              <td>{status}</td><td>按量计费</td><td>{item['release']}<br><button>设置定时关机</button></td>
              <td>{f'<button onclick="stop(\'{uuid}\')">关机</button>' if item['status']=='运行中' else ''}
              <button onclick="more('{uuid}')">更多</button></td></tr>''')
        total = 5 if self.broken_total else 4
        release_header = "其他" if self.broken_columns else "释放时间/停机时间"
        return f'''<!doctype html><meta charset="utf-8"><style>td{{padding:12px}} .el-message-box{{border:1px solid;padding:20px}} [hidden]{{display:none!important}}</style>
          <div class="loading-box instance-table"><div class="el-table instance-table"><div class="el-table__header-wrapper"><table><thead><tr>
          <th>实例ID /名称</th><th>状态</th><th>付费方式</th><th>{release_header}</th><th>操作</th>
          </tr></thead></table></div><div class="el-table__body-wrapper"><table><tbody>{''.join(rows)}</tbody></table></div></div>
          <div class="el-pagination">共 {total} 条 <button class="btn-next" {'disabled' if page==2 else ''} onclick="location.href='?page_index=2&page_size=10'">下一页</button></div></div>
          <div class="el-dropdown-menu" hidden><button onclick="start()">无卡模式开机</button></div>
          <div class="el-message-box" hidden><span id="message"></span><button onclick="submit()">确定</button></div>
          <script>
          let target='', action='';
          function more(id){{target=id;document.querySelector('.el-dropdown-menu').hidden=false}}
          function start(){{ action='power_on';document.querySelector('.el-dropdown-menu').hidden=true;
             document.querySelector('#message').textContent='确认无卡模式开机吗？无卡模式配置为:0.5核心CPU，2GB内存，无GPU卡';
             document.querySelector('.el-message-box').hidden=false; }}
          function stop(id){{target=id;action='power_off';document.querySelector('#message').textContent='确认关机吗？实例：'+id;
             document.querySelector('.el-message-box').hidden=false;}}
          async function submit(){{ await fetch('/api/v1/instance/'+action,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{instance_uuid:target,...(action==='power_on'?{{payload:'non_gpu'}}:{{}})}})}});
             document.querySelector('.el-message-box').hidden=true; }}
          </script>'''


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        # Installed Edge avoids a separate Chromium download.
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def site_console(browser):
    site = FakeSite()
    context = browser.new_context()
    context.route("**/*", site.route)
    page = context.new_page()
    settings = Settings(console_url="http://127.0.0.1:18937/console/instance/list", timeout_seconds=30, poll_seconds=1)
    console = AutoDLConsole(page, settings)
    yield site, console
    context.close()


def test_real_adapter_reads_every_page_and_serially_refreshes(site_console, tmp_path):
    site, console = site_console
    report = RefreshEngine(console, Store(tmp_path), console.settings).run()
    assert not report.errors, report.errors
    assert len(report.instances) == 4
    assert report.successes == 2
    assert site.events == [("start", site.items[0]["uuid"]), ("stop", site.items[0]["uuid"]),
                           ("start", site.items[3]["uuid"]), ("stop", site.items[3]["uuid"])]


def test_preview_has_no_power_requests(site_console, tmp_path):
    site, console = site_console
    report = RefreshEngine(console, Store(tmp_path), console.settings).run(preview=True)
    assert len(report.instances) == 4
    assert not report.errors
    assert site.events == []


@pytest.mark.parametrize("attribute", ["broken_total", "broken_columns"])
def test_incomplete_or_changed_table_prevents_mutation(site_console, tmp_path, attribute):
    site, console = site_console
    setattr(site, attribute, True)
    report = RefreshEngine(console, Store(tmp_path), console.settings).run()
    assert report.errors
    assert site.events == []


def test_login_expiry_is_detected(site_console):
    _, console = site_console
    console.page.goto("http://127.0.0.1:18937/login")
    with pytest.raises(SessionExpired):
        console.wait_ready()


def test_server_rejection_halts_queue(site_console, tmp_path):
    site, console = site_console
    site.rejected = True
    store = Store(tmp_path)
    report = RefreshEngine(console, store, console.settings).run()
    assert report.errors
    assert site.events == []
    assert not store.pending()


def test_duplicate_id_across_pages_halts_before_any_mutation(site_console, tmp_path):
    site, console = site_console
    site.items[3] = site.items[0].copy()
    report = RefreshEngine(console, Store(tmp_path), console.settings).run()
    assert report.errors
    assert site.events == []


def test_instance_moves_to_another_page_during_start(site_console, tmp_path):
    site, console = site_console
    site.reorder_on_start = True
    report = RefreshEngine(console, Store(tmp_path), console.settings).run()
    assert not report.errors, report.errors
    assert report.successes == 2
    assert [event for event, _ in site.events] == ["start", "stop", "start", "stop"]


def test_authentication_state_can_be_saved_and_reused(browser, tmp_path):
    from autodl_fresh.browser import save_auth
    store = Store(tmp_path)
    context = browser.new_context()
    context.add_cookies([{"name": "fixture_session", "value": "fixture_only", "domain": ".autodl.com", "path": "/"},
                         {"name": "unrelated", "value": "exclude", "domain": "example.com", "path": "/"}])
    save_auth(context, store)
    context.close()
    restored = browser.new_context(storage_state=str(store.auth_path))
    assert [cookie["name"] for cookie in restored.cookies()] == ["fixture_session"]
    restored.close()


def test_realistic_adjacent_div_id_does_not_require_text_boundaries(site_console):
    site, console = site_console
    uuid = site.items[0]["uuid"]
    site.items[0]["name"] = "RAMP5"
    console.open()
    row = console._row(uuid)
    assert row.count() == 1
    assert uuid + "RAMP5" in row.text_content()
    assert site.events == []


def test_preflight_failure_never_marks_the_confirmation_submitted(site_console):
    site, console = site_console
    calls = []
    site.broken_columns = True
    with pytest.raises(Exception):
        console.start_no_gpu(site.items[0]["uuid"], lambda: calls.append("accepted"),
                             submitted=lambda: calls.append("submitted"))
    assert calls == []
    assert site.events == []


def test_current_row_is_fully_scrolled_into_view(site_console):
    site, console = site_console
    console.open()
    console.page.set_viewport_size({"width": 900, "height": 600})
    console.page.add_style_tag(content=".el-table__body-wrapper {height:220px; overflow:auto} .el-table__row {height:160px}")
    row = console._focus_row(site.items[1]["uuid"])
    bounds = row.bounding_box()
    container = console.page.locator(console.TABLE).locator(".el-table__body-wrapper").bounding_box()
    assert bounds["y"] >= container["y"] - 1
    assert bounds["y"] + bounds["height"] <= container["y"] + container["height"] + 1
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 600
    assert site.events == []


def test_lookup_after_moving_to_an_earlier_page_leaves_target_page_open(site_console):
    site, console = site_console
    console.scan()
    target = site.items.pop()
    site.items.insert(0, target)
    result = console.get(target["uuid"])
    assert result.page == 1
    assert console._row(target["uuid"]).count() == 1


def test_full_refresh_rechecks_and_refreshes_time_sufficient_instances_across_pages(site_console, tmp_path):
    site, console = site_console
    site.items[1]["release"] = "14天23小时59分后释放"
    report = RefreshEngine(console, Store(tmp_path), console.settings).run(refresh_all=True)
    assert not report.errors, report.errors
    assert report.successes == 3
    expected = [item["uuid"] for item in site.items if item["uuid"] != site.items[2]["uuid"]]
    assert site.events == [(action, uuid) for uuid in expected for action in ("start", "stop")]


def test_full_refresh_rechecks_running_state_immediately_before_the_menu(site_console, tmp_path):
    site, console = site_console
    original_get = console.get
    calls = []
    def changed_get(uuid):
        calls.append(uuid)
        if len(calls) == 2:
            site.items[0].update(status="运行中", no_gpu=False, release="关机15天后释放")
        return original_get(uuid)
    console.get = changed_get
    report = RefreshEngine(console, Store(tmp_path), console.settings).run(refresh_all=True)
    assert report.errors
    assert site.events == []
