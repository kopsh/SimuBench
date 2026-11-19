"""Checks of the definition's wiring: ports, media and directions (D67)."""

from __future__ import annotations

from ...models.definition import InstrumentDefinition
from ..component import Component
from ..portref import ladder, split
from ..validate import Report, _ptr
from .lookups import _declared, _has_port

_MEDIUM_NAMES = {"signal": "模拟信号", "electrical": "电气端口", "reference": "参考信号"}


def _check_wiring(d: InstrumentDefinition, classes: dict[str, type[Component]], report: Report) -> None:
    """Signal wiring and exposed ports."""

    def check_end(ref: str, pointer: str, in_channel: bool) -> None:
        comp, port = split(ref)
        if in_channel:  # inside a channel, components are named without the ch<n>. prefix

            def cls_of(name: str) -> type[Component] | None:
                return classes.get(f"ch1.{name}")

            options = [s.name for s in d.channel_components]
        else:

            def cls_of(name: str) -> type[Component] | None:
                return classes.get(name)

            options = [s.name for s in d.components] + [f"ch{{n}}.{s.name}" for s in d.channel_components]
        owner_hint, port_hint = ladder(ref, options, lambda name: getattr(cls_of(name), "ports", ()))
        cls = cls_of(comp)
        if not comp or cls is None:
            if comp and _declared(d, comp, in_channel):
                return  # an unknown component type, reported above
            report.add(
                "error",
                "E-DEF-WIRE-COMPONENT",
                pointer,
                f"{ref!r}：没有名为 {comp or ref!r} 的组件",
                f"改为 {owner_hint}.{port}" if owner_hint else "写作 <组件>.<端口>",
            )
            return
        if not _has_port(cls, port):
            report.add(
                "error",
                "E-DEF-WIRE-PORT",
                pointer,
                f"{ref!r}：{cls.type_name} 没有端口 {port!r}",
                f"改为 {comp}.{port_hint}" if port_hint else f"可用端口：{', '.join(cls.ports) or '无'}",
            )

    def medium_of(ref: str, in_channel: bool) -> str | None:
        """What the port at a wiring end carries; None when the end does not resolve (reported above)."""
        comp, port = split(ref)
        cls = classes.get(f"ch1.{comp}" if in_channel else comp)
        spec = cls.spec_of(port) if cls is not None else None
        return spec.medium if spec is not None else None

    fed: set[str] = set()  # samples inputs a wire feeds, as ch1.<component>.<port> / <component>.<port>

    def check_media(src: str, dst: str, pointer: str, in_channel: bool) -> None:
        a, b = medium_of(src, in_channel), medium_of(dst, in_channel)
        if b == "samples":
            fed.add(f"ch1.{dst}" if in_channel else dst)
        if a is None or b is None or (a == "samples") == (b == "samples"):
            return
        if a == "samples":
            message = f"{src!r} 输出采样流，{dst!r} 接收的是{_MEDIUM_NAMES[b]}：采样流只能接到采样流输入"
            hint = "接到一个采样流（samples）输入端口；数字域没有回到模拟域的块"
        else:
            message = f"{src!r} 输出的是{_MEDIUM_NAMES[a]}，{dst!r} 是采样流输入：两者之间必须有 ADC"
            hint = "在两者之间接一个 ADC 块（输入 signal，输出 samples）"
        report.add("error", "E-DEF-WIRE-MEDIUM", pointer, message, hint)

    def check_exposed(target: str, pointer: str, in_channel: bool) -> None:
        if medium_of(target, in_channel) == "samples":
            report.add(
                "error",
                "E-DEF-WIRE-MEDIUM",
                pointer,
                f"{target!r} 是采样流端口：采样流只在仪表内部传递，不能映射为仪表的端口",
                "映射一个 electrical、signal 或 reference 端口",
            )

        comp, port = split(target)
        cls = classes.get(f"ch1.{comp}" if in_channel else comp)
        if cls is not None and port in cls.tuned_ports:
            report.add(
                "error",
                "E-DEF-PORT-TUNED",
                pointer,
                f"{target!r} 的信号取决于调谐点（本振调到哪里）：仪表之外没有调谐点，不能映射为仪表的端口",
                "映射混频器之前的端口，或输入端口的电气引脚",
            )

    for i, (src, dst) in enumerate(d.wiring.channel):
        for k, ref in enumerate((src, dst)):
            check_end(ref, _ptr("wiring", "channel", i, k), True)
        check_media(src, dst, _ptr("wiring", "channel", i), True)
    for i, (src, dst) in enumerate(d.wiring.instrument):
        for k, ref in enumerate((src, dst)):
            check_end(ref.replace("{n}", "1"), _ptr("wiring", "instrument", i, k), False)
        check_media(src.replace("{n}", "1"), dst.replace("{n}", "1"), _ptr("wiring", "instrument", i), False)
    for pname, target in d.ports.channel.items():
        check_end(target, _ptr("ports", "channel", pname), True)
        check_exposed(target, _ptr("ports", "channel", pname), True)
    for pname, target in d.ports.instrument.items():
        check_end(target.replace("{n}", "1"), _ptr("ports", "instrument", pname), False)
        check_exposed(target.replace("{n}", "1"), _ptr("ports", "instrument", pname), False)
    for section, specs, prefix in (
        ("components", d.components, ""),
        ("channel_components", d.channel_components, "ch1."),
    ):
        for i, spec in enumerate(specs):
            cls = classes.get(prefix + spec.name)
            for port, ps in (cls.ports if cls else {}).items():
                if (
                    ps.medium == "samples"
                    and ps.direction == "in"
                    and f"{prefix}{spec.name}.{port}" not in fed
                ):
                    report.add(
                        "error",
                        "E-DEF-SAMPLES-OPEN",
                        _ptr(section, i),
                        f"{spec.name}.{port} 是采样流输入，没有连线接到它：采样流输入不能悬空",
                        f"在 wiring 中加一条连线，把上游块的采样流输出接到 {spec.name}.{port}",
                    )
