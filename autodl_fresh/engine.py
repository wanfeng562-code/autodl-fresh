from __future__ import annotations

import threading

from .browser import ActionRejected, Cancelled, ConsoleError, reset_verified
from .models import RunReport, now
from .storage import Settings, Store


class RefreshEngine:
    def __init__(self, console, store: Store, settings: Settings, log=None, changed=None, cancel=None):
        self.console = console
        self.store = store
        self.settings = settings
        self.log = log or (lambda _: None)
        self.changed = changed or (lambda _: None)
        self.cancel = cancel or threading.Event()

    def _mark_accepted(self):
        pending = self.store.pending()
        pending["phase"] = "accepted"
        self.store.set_pending(pending)

    def _mark_submitted(self):
        pending = self.store.pending()
        pending["phase"] = "submitted"
        self.store.set_pending(pending)
        self.log(f"{pending['uuid']}：尝试提交无卡开机确认")

    def _finish_pending(self):
        pending = self.store.pending()
        if not pending:
            return None
        uuid = pending["uuid"]
        before = pending["before_seconds"]
        current = self.console.get(uuid)
        if pending["phase"] != "accepted":
            raise ConsoleError(f"{uuid} 的上次开机响应未确认。请在网页核对并关机，再点“检查并刷新”核验倒计时；状态未确认前暂停后续开机。")
        retention = pending.get("retention_days", 15)
        if current.status in {"开机中", "运行中"}:
            self.log(f"{uuid}：等待本应用启动的无卡实例就绪，随后关机")
            running = self.console.wait(uuid, lambda item: item.status == "运行中", "无卡实例运行")
            if not running.no_gpu:
                raise ConsoleError(f"{uuid} 未显示无卡模式，暂停操作，请人工检查。")
            retention = running.retention_days
            self.store.set_pending({**pending, "retention_days": retention})
            self.console.stop(uuid)
        # Cancellation never interrupts cleanup of an already accepted power-on.
        self.console.wait(uuid, lambda item: item.status == "已关机", "关机完成")
        self.log(f"{uuid}：已确认关机，正在核验释放倒计时")
        final = self.console.wait(uuid, lambda item: reset_verified(
            item, retention, before, accepted_full_refresh=pending.get("refresh_all", False),
        ), "释放倒计时重置")
        self.store.set_pending({})
        self.log(f"{uuid}：刷新成功，{final.release_text.replace(chr(10), ' ')}")
        return final

    def run(self, *, preview=False, refresh_all=False) -> RunReport:
        report = RunReport(preview=preview, refresh_all=refresh_all)
        try:
            # Always populate the list first, even if a previous operation needs
            # recovery. Recovery failure is not evidence of an empty account.
            report.instances = self.console.scan(self.cancel)
            report.scan_complete = True
            report.snapshot_at = now().isoformat()
            self.changed(report)
            if preview:
                self.log("只读检查完成，未执行开关机。")
                return report
            if self.store.clear_legacy_preflight():
                self.log("已修复旧版定位失败留下的记录：该次尚未提交开机请求，可继续处理。")
            if self.store.pending().get("phase") == "prepared":
                self.log("上次任务在提交开机确认前结束，已解除未提交记录。")
                self.store.set_pending({})
            if not preview and self.store.pending():
                pending = self.store.pending()
                current = self.console.get(pending["uuid"])
                if pending["phase"] in {"intent", "submitted"} and current.status == "已关机":
                    # A user may have manually completed an ambiguous start/stop.
                    if reset_verified(current, pending.get("retention_days", 15), pending["before_seconds"]):
                        self.store.set_pending({})
                        report.outcomes[current.uuid] = "刷新成功"
                        report.instances = [current if item.uuid == current.uuid else item for item in report.instances]
                    else:
                        self._finish_pending()
                else:
                    final = self._finish_pending()
                    report.outcomes[final.uuid] = "刷新成功"
                    report.instances = [final if item.uuid == final.uuid else item for item in report.instances]
            busy = [item.uuid for item in report.instances if item.no_gpu and item.status != "已关机"]
            if busy:
                raise ConsoleError(f"已有无卡实例运行或启动中（{', '.join(busy)}），本轮暂停刷新。")
            candidates = [item for item in report.instances
                          if item.eligible(self.settings.threshold_days, refresh_all=refresh_all)
                          and report.outcomes.get(item.uuid) != "刷新成功"]
            if refresh_all:
                self.log("全部刷新：忽略剩余天数阈值，仅处理状态和释放倒计时均可确认的已关机按量实例。")
            self.log(f"完整检查 {len(report.instances)} 个实例，{len(candidates)} 个符合刷新条件")
            for instance in candidates:
                if self.cancel.is_set():
                    report.cancelled = True
                    break
                current = self.console.get(instance.uuid)
                if not current.eligible(self.settings.threshold_days, refresh_all=refresh_all):
                    report.outcomes[instance.uuid] = "状态已变化，跳过"
                    continue
                self.store.set_pending({
                    "uuid": instance.uuid, "phase": "prepared", "created_at": now().isoformat(),
                    "before_seconds": current.remaining_seconds, "retention_days": 15,
                    "refresh_all": refresh_all,
                })
                report.outcomes[instance.uuid] = "开机前检查"
                self.changed(report)
                try:
                    self.log(f"{instance.uuid}：检查无卡开机条件与确认弹窗")
                    options = {"refresh_all": True} if refresh_all else {}
                    self.console.start_no_gpu(instance.uuid, self._mark_accepted,
                                              submitted=self._mark_submitted, **options)
                    final = self._finish_pending()
                    report.instances = [final if item.uuid == final.uuid else item for item in report.instances]
                    report.outcomes[instance.uuid] = "刷新成功"
                    self.changed(report)
                except ActionRejected as exc:
                    if self.store.pending().get("phase") in {"prepared", "submitted"}:
                        self.store.set_pending({})
                    report.outcomes[instance.uuid] = "操作失败"
                    raise ConsoleError(str(exc)) from exc
                except Exception:
                    # Try once to stop a power-on the platform explicitly accepted.
                    # If cleanup cannot be verified, retain the journal and stop the queue.
                    if self.store.pending().get("phase") == "accepted":
                        self.log(f"{instance.uuid}：发生异常，优先尝试完成关机和核验")
                        final = self._finish_pending()
                        report.instances = [final if item.uuid == final.uuid else item for item in report.instances]
                        report.outcomes[instance.uuid] = "刷新成功"
                        self.changed(report)
                    else:
                        if self.store.pending().get("phase") == "prepared":
                            self.store.set_pending({})
                            report.outcomes[instance.uuid] = "开机前检查失败（未提交请求）"
                        raise
        except Cancelled:
            report.cancelled = True
        except Exception as exc:
            message = str(exc)
            report.errors.append(message)
            if pending := self.store.pending():
                report.outcomes[pending["uuid"]] = "待人工检查 / 下次优先核验"
        finally:
            report.finished_at = now().isoformat()
            self.store.append_report(report)
            self.changed(report)
        return report
