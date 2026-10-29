"""Clone through AutoDL's visible controls; observe, never replay, its order API."""
from __future__ import annotations

from decimal import Decimal
import re
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from .browser import ActionRejected, Cancelled, ConsoleError, InventoryUnavailable, LayoutChanged, SessionExpired
from .models import compact
from .rental import GPUOffer, RentalOptions, StockLost, money


class CloneConsole:
    ROOT = ".create-wrap"
    FILTER = ".select-server .list-filter"
    TABLE = ".machine-table"
    ROWS = ".el-table__body-wrapper tbody tr.el-table__row"
    ORDER_PATH = "/api/v1/order/instance/clone/payg"
    RESPONSE_TIMEOUT = 25000
    MACHINE_PATH = "/api/v1/user/machine/list"

    def __init__(self, console):
        self.console, self.page = console, console.page
        self.source = None
        self.url = ""
        self.region_sign = ""
        self.region_value = ""
        self.expand_bytes = 0
        self.statuses = {}
        self.price_key = None
        self.price_hourly = None
        self.price_disk_daily = None
        self.machine_pending = set()
        self.machine_failed = False
        self.machine_latest = None
        self.machine_sequence = 0
        self.machine_success = 0
        self.machine_auth_failed = False
        self.on_running = lambda task: None
        self.cancel = None
        self.page_crashed = False
        self.page.on("crash", lambda *_: setattr(self, "page_crashed", True))
        self.sorted_idle_floor = None
        self.order_attempt = None
        self.order_guard_installed = False
        self.page.on("response", self._observe_instances)
        self.page.on("response", self._observe_quote)
        self.page.on("request", self._machine_request)
        self.page.on("requestfinished", lambda r: self.machine_pending.discard(r))
        self.page.on("requestfailed", self._machine_failed)
        self.page.on("response", self._machine_response)

    def browser_state(self):
        browser = self.page.context.browser
        return {"page_closed": self.page.is_closed(), "page_crashed": self.page_crashed,
                "browser_connected": bool(browser and browser.is_connected())}

    def _machine_request(self, request):
        if urlsplit(request.url).path == self.MACHINE_PATH:
            self.machine_pending.add(request)
            self.machine_latest = request
            self.machine_sequence += 1
            self.machine_failed = False
            self.sorted_idle_floor = None

    def _machine_failed(self, request):
        if request in self.machine_pending:
            if request is self.machine_latest and request.failure != "net::ERR_ABORTED":
                self.machine_failed = True
            self.machine_pending.discard(request)

    def _machine_response(self, response):
        if urlsplit(response.url).path == self.MACHINE_PATH and response.request is self.machine_latest:
            try:
                payload = response.json()
                if response.request is not self.machine_latest:
                    return
                self.machine_auth_failed = response.status in {401, 403} or payload.get("code") == "AuthorizeFailed"
                self.machine_failed = response.status != 200 or payload.get("code") != "Success"
                if not self.machine_failed:
                    self.machine_success = self.machine_sequence
                    self._observe_idle_sort(response, payload)
            except (ValueError, TypeError, KeyError, AttributeError, PlaywrightError):
                if response.request is self.machine_latest:
                    self.machine_failed = True

    def _observe_idle_sort(self, response, payload):
        # This optional shortcut must never turn a successfully loaded table
        # into a failed request when pagination metadata is absent or changes.
        try:
            body = response.request.post_data_json
            data = payload.get("data") or {}
            rows = data.get("list")
            if isinstance(body, dict) and body.get("gpu_idle_type") == "desc" and not body.get("machine_id"):
                if isinstance(rows, list):
                    idle = [int(row["gpu_idle_num"]) for row in rows]
                    if idle == sorted(idle, reverse=True):
                        self.sorted_idle_floor = min(idle) if idle else 0
        except (ValueError, TypeError, KeyError, AttributeError, PlaywrightError):
            pass

    def _new_table_round(self):
        # Old, cancelled navigations must never poison the next table load.
        self.machine_pending.clear()
        self.machine_latest = None
        self.machine_failed = False
        self.machine_auth_failed = False
        self.sorted_idle_floor = None

    def show_status(self, task):
        if self.console.settings.browser_visibility != "foreground" or urlsplit(self.page.url).path != "/cloneInstance":
            return
        try:
            self.page.evaluate("""message=>{
                let banner=document.getElementById('autodl-fresh-rental-status');
                if(!banner){
                    banner=document.createElement('div'); banner.id='autodl-fresh-rental-status';
                    banner.setAttribute('role','status');
                    banner.style.cssText='position:fixed;top:12px;left:50%;transform:translateX(-50%);'
                        +'z-index:2147483647;max-width:85vw;padding:12px 20px;border-radius:9px;'
                        +'background:#e6f5eb;color:#145b3b;border:1px solid #88c7a2;'
                        +'box-shadow:0 4px 20px #0002;font:15px system-ui;pointer-events:none;';
                    document.body.appendChild(banner);
                }
                banner.textContent='AutoDL Fresh · '+message;
            }""", task.get("message", "正在候租"))
        except PlaywrightError:
            pass

    def _observe_quote(self, response):
        if urlsplit(response.url).path != "/api/v1/order/price/preview":
            return
        self.price_key, self.price_hourly, self.price_disk_daily = None, None, None
        try:
            data = response.request.post_data_json
            payload = response.json()
            if response.status == 200 and payload.get("code") == "Success":
                values = payload["data"]
                for discount in values.get("discount_list", []):
                    if discount.get("product_type") == "instance":
                        self.price_hourly = money(money(discount["origin_price"])
                                                  - money(discount["total_reduction"])) / 1000
                disk = values.get("data_disk_info") or {}
                if "data_disk_deal_daily_price" in disk:
                    self.price_disk_daily = money(disk["data_disk_deal_daily_price"]) / 1000
                self.price_key = (data["machine_id"], int(data["num"]),
                                  int(data.get("expand_data_disk", 0)), data["charge_type"])
        except (ValueError, TypeError, KeyError, AttributeError, PlaywrightError):
            self.price_key = None

    def _observe_instances(self, response):
        if urlsplit(response.url).path != "/api/v1/instance" or response.status != 200:
            return
        try:
            payload = response.json()
            if payload.get("code") != "Success":
                return
            for item in payload.get("data", {}).get("list", []):
                # Do not persist the raw account response (contains SSH secrets).
                self.statuses[item["uuid"]] = {
                    "copy": (item.get("additional_info") or {}).get("copy_data_disk_after_clone_status"),
                    "clone": item.get("clone_status"),
                }
        except (ValueError, TypeError, KeyError, AttributeError, PlaywrightError):
            pass

    def prepare(self, uuid):
        try:
            current = urlsplit(self.page.url)
            query = parse_qs(current.query)
            if self.source and self.source.uuid == uuid and current.path == "/cloneInstance" and query.get("id") == [uuid]:
                # A retry after entering the clone page stays on that page.
                self._remember_clone_page()
                self._assert_page()
                # An earlier failed table request cannot recover by waiting on
                # itself. offers() starts a fresh request with the checkboxes.
                return self.source
            return self._prepare(uuid)
        except PlaywrightTimeoutError as exc:
            self.console._check_origin()
            raise InventoryUnavailable("克隆页面读取超时，将自动重试") from exc

    def _prepare(self, uuid):
        self._new_table_round()
        source = self.console.get(uuid)
        if source.status != "已关机" or source.blocked_reason:
            raise ConsoleError("候租的源实例必须已关机且状态正常，请先手动确认。")
        self.source = source
        self.console._row(uuid).get_by_role("button", name="更多", exact=True).click()
        self.page.locator(".el-dropdown-menu:visible").get_by_text("克隆实例", exact=True).click()
        dialog = self.page.locator(".el-dialog:visible").filter(has_text="克隆实例")
        dialog.wait_for(state="visible")
        quota = re.search(r"剩余克隆次数[：:]\s*(\d+)", dialog.inner_text())
        if not quota or int(quota[1]) <= 0:
            raise ConsoleError("今天没有可确认的剩余克隆次数，已停止。")
        for name in ("系统盘", "数据盘"):
            label = dialog.locator("label").filter(has_text=re.compile(rf"^\s*{name}\s*$"))
            checkbox = label.locator('input[type="checkbox"]')
            if checkbox.count() != 1:
                raise LayoutChanged(f"无法唯一识别{name}勾选框，已停止。")
            if not checkbox.is_checked():
                label.click()
            if not checkbox.is_checked():
                raise LayoutChanged(f"{name}未勾选，已停止。")
        dialog.get_by_role("button", name="继续", exact=True).click()
        self.page.locator(self.ROOT).wait_for(state="visible")
        self._remember_clone_page()
        self._wait_table()
        self._assert_page()
        return source

    def _remember_clone_page(self):
        query = parse_qs(urlsplit(self.page.url).query)
        self.url = self.page.url
        self.region_sign = query.get("region", [""])[0]
        self.region_value = ""
        try:
            self.expand_bytes = int(query.get("expand_size", ["0"])[0] or 0)
            if self.expand_bytes < 0:
                raise ValueError
        except ValueError as exc:
            raise LayoutChanged("源数据盘扩容容量无法识别") from exc

    def _assert_page(self):
        self.console._check_origin()
        url = urlsplit(self.page.url)
        query = parse_qs(url.query)
        if (url.path != "/cloneInstance" or query.get("id") != [self.source.uuid]
                or query.get("copy_data_disk") != ["1"] or query.get("region") != [self.region_sign]
                or not self.region_sign):
            raise LayoutChanged("克隆页的源实例、数据盘或地区不匹配，已停止。")
        if self.page.locator(self.ROOT).get_by_text(
                re.compile(r"^" + re.escape(self.source.uuid) + r"\s*/")).count() != 1:
            raise LayoutChanged("页面源实例不匹配，已停止。")
        payg = self.page.locator(self.ROOT).locator('input[type="radio"][value="payg"]')
        if payg.count() != 1 or not payg.is_checked():
            raise LayoutChanged("候租仅支持按量计费，当前计费方式不匹配。")
        regions = self.page.locator(self.FILTER).locator('.filter-item').filter(has_text="选择地区")
        # Special zones are rendered on a second row without the region label.
        regions = regions.or_(self.page.locator(self.FILTER).locator(".second-region"))
        selected = regions.locator('input[type="radio"]:checked')
        if selected.count() != 1:
            raise LayoutChanged("未能识别克隆页唯一选中地区，请等待页面加载后重试。")
        text = selected.locator("xpath=ancestor::label[1]").inner_text()
        if compact(text) != compact(self.source.region.split("/")[0]):
            raise LayoutChanged("克隆地区与源实例不同，已停止。")
        value = selected.input_value()
        # The clone URL and the market selector use different region namespaces
        # (e.g. west-B versus westDC3). Validate the source's displayed region,
        # then track the selector's own value independently of the route alias.
        if self.region_value and value != self.region_value:
            raise LayoutChanged("克隆页选中的地区已切换，已停止。")
        self.region_value = value

    def catalog(self, *, include_prices=False, cancel=None, changed=None):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._assert_page()
            values = self._gpu_checkboxes().evaluate_all(
                "els => els.map(el => el.value).filter(v => v !== 'false')")
            if values:
                if len(set(values)) != len(values):
                    raise LayoutChanged("GPU 型号控件出现重复值，无法唯一选择，请检查网页")
                result = {"source": self.source.to_dict(), "gpu_names": values}
                if include_prices:
                    prices, errors = {}, {}
                    for index, name in enumerate(values):
                        self._check_cancel(cancel)
                        self.console.log(f"读取网页 GPU 报价 {index + 1}/{len(values)}：{name}")
                        self._set_models([name])
                        # Disabled (busy) hosts still display their price.
                        rows = self._read_offers(include_unavailable=True)
                        if rows:
                            rates = [money(row.hourly_price) for row in rows]
                            prices[name] = {"min": str(min(rates)), "max": str(max(rates))}
                        else:
                            errors[name] = "网页筛选后没有主机报价"
                        if changed:
                            changed({**result, "gpu_prices": prices,
                                     "price_errors": errors,
                                     "progress": f"正在逐个读取网页报价（{index + 1}/{len(values)}）…"})
                    result.update(gpu_prices=prices, price_errors=errors)
                return result
            # Region and host-table requests can finish before GPU-type options.
            self.page.wait_for_timeout(100)
        raise LayoutChanged("GPU 型号列表尚未加载完成，请检查网络后重试")

    def _gpu_checkboxes(self):
        group = self.page.locator(self.FILTER).locator(".filter-item").filter(has_text="GPU型号")
        return group.locator('input[type="checkbox"]')

    def _wait_table(self, *, after=None):
        self._check_cancel(self.cancel)
        table = self.page.locator(self.TABLE)
        table.wait_for(state="visible")
        # Vue filter watchers begin asynchronously; then wait for loading to end.
        self.page.wait_for_timeout(250)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_cancel(self.cancel)
            self.console._check_origin()
            if self.machine_auth_failed:
                raise SessionExpired("AutoDL 登录态已过期，请重新登录")
            if self.machine_failed:
                raise InventoryUnavailable("当前网页筛选请求暂未成功，将自动重试")
            if (not table.locator(".el-loading-mask:visible").count()
                    and self.machine_latest not in self.machine_pending
                    and (after is None or self.machine_success > after)):
                self.page.wait_for_timeout(50)
                return
            self.page.wait_for_timeout(100)
        raise InventoryUnavailable("当前网页筛选请求尚未完成，将自动重试")

    @staticmethod
    def _check_cancel(cancel):
        if cancel and cancel.is_set():
            raise Cancelled("已停止读取，未提交创建")

    def _set_models(self, names):
        self._assert_page()
        checkboxes = self._gpu_checkboxes()
        for control in checkboxes.all():
            self._check_cancel(self.cancel)
            value = control.input_value()
            if value == "false":
                continue
            wanted = value in names
            if control.is_checked() != wanted:
                before = self.machine_sequence
                control.locator("xpath=ancestor::label[1]").click()
                self._wait_table(after=before)
        selected = checkboxes.evaluate_all("es=>es.filter(e=>e.checked && e.value!=='false').map(e=>e.value)")
        if set(selected) != set(names):
            raise LayoutChanged("GPU 型号勾选状态不匹配，已停止")
        self._assert_page()

    def _apply_filters(self, options):
        available = self.catalog()["gpu_names"]
        if any(name not in available for name in options.gpu_names):
            raise LayoutChanged("部分候租 GPU 型号已不在本地区列表，请重新读取型号并配置。")
        self._set_models(options.gpu_names)
        group = self.page.locator(self.FILTER).locator('.filter-item').filter(has_text="GPU数量")
        control = group.locator(f'input[type="radio"][value="{options.gpu_count}"]')
        if control.count() != 1 or not control.is_enabled():
            raise LayoutChanged("目标 GPU 卡数不可选择")
        if not control.is_checked():
            before = self.machine_sequence
            control.locator("xpath=..").click()
            self._wait_table(after=before)

    def _headers(self):
        headers = self.page.locator(self.TABLE).locator(".el-table__header-wrapper th").all_inner_texts()
        result = {}
        for i, header in enumerate(headers):
            for name, text in {"gpu": "算力型号", "idle": "空闲GPU", "price": "价格"}.items():
                if compact(header).startswith(text):
                    result[name] = i
        if len(result) != 3:
            raise LayoutChanged("机器表格列已变化，无法可靠识别型号、空闲卡数与报价")
        return result

    def _read_offers(self, *, include_unavailable=False):
        columns = self._headers()
        result = []
        # One synchronous browser-side snapshot. Positional Locator.all() calls
        # can wait on a row that Vue removed between two Python RPCs.
        rows = self.page.locator(self.TABLE).locator(self.ROWS).evaluate_all("""rows=>rows.map(row=>({
            radios:[...row.querySelectorAll('input[type="radio"]')].map(r=>({
                value:r.value,disabled:r.disabled || !!r.closest('.is-disabled,[aria-disabled="true"]')
            })),
            cells:[...row.querySelectorAll('td')].map(c=>c.innerText),
            alias:[...row.querySelectorAll('.machineId,.tide')].map(c=>c.innerText).join(' ')
        }))""")
        for row in rows:
            if len(row["radios"]) != 1:
                raise LayoutChanged("机器行的唯一 ID 无法识别")
            radio = row["radios"][0]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", radio["value"]):
                raise LayoutChanged("机器行的 ID 格式无法识别")
            if not include_unavailable and radio["disabled"]:
                continue
            cells = row["cells"]
            if len(cells) <= max(columns.values()):
                raise InventoryUnavailable("主机表格正在重新生成，将重新检查")
            name = cells[columns["gpu"]].strip().splitlines()[0].strip()
            idle = re.fullmatch(r"(\d+)/(\d+)", compact(cells[columns["idle"]]))
            price = re.search(r"[￥¥]\s*(\d+(?:\.\d+)?)\s*/(?:时|小时)", cells[columns["price"]])
            if not idle or not price:
                raise LayoutChanged("可租机器的空闲卡数或小时单价无法识别")
            result.append(GPUOffer(radio["value"], name, int(idle[1]), price[1], row["alias"]))
        return result

    def offers(self, options, cancel):
        try:
            return self._offers(options, cancel)
        except PlaywrightTimeoutError as exc:
            self.console._check_origin()
            raise InventoryUnavailable("网页筛选操作暂未完成，将自动重试") from exc

    def _offers(self, options, cancel):
        self._check_cancel(cancel)
        self._assert_page()
        self._apply_filters(options)
        # Put idle cards first through the website's own sort control. This lets
        # an empty stock check finish without scrolling hundreds of busy hosts.
        header = self.page.locator(self.TABLE).locator(".el-table__header-wrapper th").nth(self._headers()["idle"])
        descending = header.locator(".sort-caret.descending")
        if descending.count() == 1 and "descending" not in (header.get_attribute("class") or ""):
            before = self.machine_sequence
            descending.click()
            self._wait_table(after=before)
        last = options.gpu_names[-1]
        # Keep every requested type selected, refreshing only the final type.
        for wanted in (False, True):
            self._check_cancel(cancel)
            control = next(c for c in self._gpu_checkboxes().all() if c.input_value() == last)
            if control.is_checked() != wanted:
                before = self.machine_sequence
                control.locator("xpath=ancestor::label[1]").click()
                self._wait_table(after=before)
        self._check_cancel(cancel)
        return self._collect_offers(options, cancel)

    def _collect_offers(self, options, cancel):
        found, previous = {}, None
        for _ in range(300):
            self._check_cancel(cancel)
            self._assert_page()
            for item in self._read_offers():
                found[item.machine_id] = item
            if any(item.gpu_name == options.gpu_names[0] and item.idle >= options.gpu_count
                   for item in found.values()):
                return list(found.values())
            if self.sorted_idle_floor is not None and self.sorted_idle_floor < options.gpu_count:
                return list(found.values())
            rows = self.page.locator(self.TABLE).locator(self.ROWS)
            signature = tuple(rows.locator('input[type="radio"]').evaluate_all("es=>es.map(e=>e.value)"))
            self.page.locator(self.TABLE).locator(".el-table__body-wrapper").evaluate(
                "e=>{e.scrollTop=e.scrollHeight;e.dispatchEvent(new Event('scroll'))}")
            self.page.wait_for_timeout(100)
            self._wait_table()
            current = tuple(rows.locator('input[type="radio"]').evaluate_all("es=>es.map(e=>e.value)"))
            if current == signature and previous == signature:
                return list(found.values())
            previous = signature
        raise LayoutChanged("机器滚动列表超过读取上限，已停止候租")

    def _find_available(self, offer):
        previous = None
        for _ in range(300):
            self._assert_page()
            for current in self._read_offers():
                if current.machine_id == offer.machine_id:
                    return current
            rows = self.page.locator(self.TABLE).locator(self.ROWS)
            signature = tuple(rows.locator('input[type="radio"]').evaluate_all("es=>es.map(e=>e.value)"))
            scroll = self.page.locator(self.TABLE).locator(".el-table__body-wrapper")
            scroll.evaluate("e=>{e.scrollTop=e.scrollHeight; e.dispatchEvent(new Event('scroll'))}")
            self.page.wait_for_timeout(350)
            self._wait_table()
            current = tuple(rows.locator('input[type="radio"]').evaluate_all("es=>es.map(e=>e.value)"))
            if current == signature and previous == signature:
                return None
            previous = signature
        raise LayoutChanged("机器滚动列表超过读取上限，已停止候租")

    def _machine_row(self, machine_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", machine_id):
            raise LayoutChanged("目标主机 ID 格式无法识别")
        identity = self.page.locator(f'input[type="radio"][value="{machine_id}"]')
        rows = self.page.locator(self.TABLE).locator(self.ROWS).filter(has=identity)
        if rows.count() != 1:
            raise InventoryUnavailable("目标主机列表已变化，将重新检查")
        return rows

    def _quoted_cost(self):
        text = compact(self.page.locator(".pay-wrap .sum").inner_text())
        match = re.search(r"配置费用[：:][￥¥](\d+(?:\.\d+)?)/(?:时|小时)", text)
        if not match:
            raise LayoutChanged("无法确认最终每小时配置费用，未提交创建")
        if self.price_hourly is None:
            raise LayoutChanged("未读取到平台配置费用的精确报价，未提交创建")
        hourly = max(money(match[1]), self.price_hourly)
        daily = Decimal(0)
        disk = self.page.locator(".data-disk")
        expansion = disk.locator('input[type="text"]')
        size_gb = money(expansion.first.input_value() or 0) if expansion.count() else Decimal(0)
        if size_gb * 1024**3 < self.expand_bytes:
            raise LayoutChanged("目标数据盘扩容容量小于源实例，未提交创建")
        if size_gb > 0:
            rate = re.search(r"(\d+(?:\.\d+)?)元/日/GB", compact(disk.inner_text()))
            if not rate:
                raise LayoutChanged("无法识别数据盘每日扩容费用，未提交创建")
            daily = money(rate[1]) * size_gb
            if self.price_disk_daily is None:
                raise LayoutChanged("未读取到平台数据盘每日精确报价，未提交创建")
            daily = max(daily, self.price_disk_daily)
        return {"hourly": str(hourly), "disk_daily": str(daily), "expand_gb": str(size_gb)}

    def select(self, offer, options):
        try:
            return self._select(offer, options)
        except PlaywrightTimeoutError as exc:
            self.console._check_origin()
            raise InventoryUnavailable("选机页面读取超时，将自动重试") from exc

    def _select(self, offer, options):
        self.price_key = None
        self._assert_page()
        current = self._find_available(offer)
        if not current or current.gpu_name != offer.gpu_name or current.idle < options.gpu_count:
            return None
        row = self._machine_row(offer.machine_id)
        row.scroll_into_view_if_needed()
        row.evaluate("e=>e.scrollIntoView({block:'center',inline:'nearest'})")
        row.click()
        # The second GPU-count selector is for the chosen host's rent rules.
        group = self.page.locator(".gpu-num")
        if group.count():
            control = group.locator(f'input[type="radio"][value="{options.gpu_count}"]')
            if control.count() != 1 or not control.is_enabled():
                return None
            if not control.is_checked():
                control.locator("xpath=..").click()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
            if self.price_key is None:
                continue
            expansion = self.page.locator(".data-disk").locator('input[type="text"]')
            size_gb = money(expansion.first.input_value() or 0) if expansion.count() else Decimal(0)
            expected = (offer.machine_id, options.gpu_count,
                        int(size_gb * 1024**3), "payg")
            if self.price_key == expected:
                quote = self._quoted_cost()
                break
        else:
            raise LayoutChanged("未收到所选主机和卡数的最新报价，未提交创建")
        if not row.locator('input[type="radio"]').is_checked():
            raise LayoutChanged("目标主机选择未生效")
        return quote

    def create(self, offer: GPUOffer, options: RentalOptions, submitted):
        self._assert_page()
        quote = self._quoted_cost()
        row = self._machine_row(offer.machine_id)
        if not row.locator('input[type="radio"]').is_checked():
            raise StockLost("选中主机已变化，正在重新检查库存", code="SelectionChanged")
        # One persistent gate per work page. A new attempt replaces its state;
        # it never falls through a stack of closed guards from older attempts.
        if not self.order_guard_installed:
            self.page.route("**/api/v1/order/**", self._guard_order)
            self.order_guard_installed = True
        attempt = {"allowed": True, "offer": offer, "options": options, "quote": quote, "forwarded": []}
        self.order_attempt = attempt
        try:
            submitted()
            with self.page.expect_response(lambda r: urlsplit(r.url).path == self.ORDER_PATH
                                           and r.request in attempt["forwarded"], timeout=self.RESPONSE_TIMEOUT) as event:
                self.page.locator(".pay-wrap").get_by_role("button", name="创建并开机", exact=True).click()
            response = event.value
            payload = response.json()
            if (response.status != 200 or not isinstance(payload, dict)
                    or not isinstance(payload.get("code"), str) or not payload["code"]):
                raise ConsoleError("创建响应不完整，请在网页核对，暂停新的租用")
            if payload["code"] != "Success":
                # A returned target or other nonempty result contradicts a
                # clean rejection. Preserve the journal instead of retrying.
                if payload.get("data") not in (None, "", {}, []):
                    raise ConsoleError("创建响应未确认成功且含有结果数据，请先在网页核对，暂停新的租用")
                code = payload["code"]
                if self._stock_rejection(code, payload.get("msg", "")):
                    raise StockLost("该主机空闲 GPU 已不足或被其他用户租走，平台未接受此次创建", code=code)
                safe_code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else "Unknown"
                raise ActionRejected(f"平台拒绝创建（错误码：{safe_code}），请检查余额、权限、实名认证或克隆次数")
            uuid = payload.get("data")
            if not isinstance(uuid, str) or not self.console.ID_RE.fullmatch(uuid) or uuid == options.source_uuid:
                raise ConsoleError("平台返回成功但新实例 ID 无法识别，请先在网页核对")
            return uuid
        except PlaywrightTimeoutError as exc:
            if not attempt["forwarded"]:
                raise ActionRejected("未发出有效创建请求，请检查页面是否出现兼容性或余额提示") from exc
            raise ConsoleError("创建请求已发送但响应未确认，禁止重复下单；请在网页核对") from exc
        finally:
            # The gate stays installed, closed between attempts and after a
            # timeout. A late website preflight must not submit a stray order.
            attempt["allowed"] = False

    @staticmethod
    def _stock_rejection(code, message):
        message = compact(message[:1000]).upper() if isinstance(message, str) else ""
        # Permanent account failures take precedence over textual stock hints.
        if code in {"TORecharge", "TORealName", "AuthorizeFailed", "PermissionDenied", "Forbidden"}:
            return False
        if any(word in message for word in ("余额", "钱包", "实名", "权限", "克隆次数", "配额", "BALANCE", "QUOTA")):
            return False
        # MachineGpuNumUpdateFailed is the stock code handled by AutoDL's own
        # frontend. A narrow message fallback covers generic error envelopes.
        return code in {"MachineGpuNumUpdateFailed", "NoStock"} or any(text in message for text in (
            "空闲GPU数量不足", "GPU数量不足", "GPU资源不足", "没有空闲GPU", "无可用GPU",
            "GPU已被占用", "GPU已被租用", "主机已被占用", "主机资源不足", "该主机资源已售罄"))

    def _guard_order(self, route):
        request = route.request
        if request.method != "POST" or urlsplit(request.url).path == "/api/v1/order/price/preview":
            route.fallback()
            return
        attempt = self.order_attempt
        valid = False
        if attempt and attempt["allowed"] and not attempt["forwarded"]:
            offer, options, quote = attempt["offer"], attempt["options"], attempt["quote"]
            try:
                data = request.post_data_json
                info, price = data["instance_info"], data["price_info"]
                valid = (urlsplit(request.url).path == self.ORDER_PATH
                         and urlsplit(request.url).netloc == urlsplit(self.console.settings.console_url).netloc
                         and data["instance_uuid"] == options.source_uuid
                         and info["machine_id"] == price["machine_id"] == offer.machine_id
                         and info["charge_type"] == price["charge_type"] == "payg"
                         and info["req_gpu_amount"] == price["num"] == options.gpu_count
                         and info["copy_data_disk_after_clone"] is True
                         and info.get("expand_data_disk", 0) == price.get("expand_data_disk", 0)
                         == int(money(quote["expand_gb"]) * 1024**3)
                         and self.price_key == (offer.machine_id, options.gpu_count,
                                                info.get("expand_data_disk", 0), "payg")
                         and self.price_hourly is not None)
            except (KeyError, TypeError, ValueError):
                pass
        if not valid:
            route.abort()
            return
        attempt["forwarded"].append(request)
        route.fallback()

    def verify(self, task):
        uuid = task["target_uuid"]
        options = RentalOptions.from_dict(task["options"])
        deadline = time.monotonic() + self.console.settings.timeout_seconds
        announced = False
        while time.monotonic() < deadline:
            self.statuses.pop(uuid, None)
            items = self.console.scan()
            item = next((x for x in items if x.uuid == uuid), None)
            status = self.statuses.get(uuid, {})
            if item:
                if (compact(item.region.split("/")[0]) != compact(task["source_region"])
                        or item.gpu_name != task["offer"]["gpu_name"] or item.gpu_count != options.gpu_count):
                    raise ConsoleError("新实例地区、GPU 型号或卡数与订单不匹配，请在网页核对")
                if item.blocked_reason or status.get("copy") in {"Failed", "Error"}:
                    raise ConsoleError("新实例克隆或数据盘拷贝异常，请在网页处理")
                if item.status == "运行中" and not item.no_gpu:
                    if not announced:
                        self.on_running({**task, "phase": "gpu_running",
                                         "message": f"GPU 已租到并运行：{uuid}，正在核验数据盘拷贝。"})
                        announced = True
                    if status.get("copy") == "Success":
                        return item
            self.page.wait_for_timeout(self.console.settings.poll_seconds * 1000)
        raise ConsoleError(f"等待 {uuid} 运行及数据盘拷贝完成超时；请点击“核验已创建实例”，不会重新下单")
