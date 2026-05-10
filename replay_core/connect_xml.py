from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True, slots=True)
class ConnectMessage:
    t_ms: int
    method: str
    args: list


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    # Values are typically inside CDATA.
    return (el.text or "").strip()


def _parse_struct(el: ET.Element) -> object:
    # Generic parse for nodes like <layouts><L1>...</L1></layouts>.
    if len(list(el)) == 0:
        return _text(el)
    # If it looks like a dict of keys.
    if all(child.tag != "Object" for child in list(el)):
        d: dict[str, object] = {}
        for child in list(el):
            d[child.tag] = _parse_struct(child)
        return d
    return [_parse_typed_value(child) for child in list(el)]


def _parse_typed_value(el: ET.Element) -> object:
    tag = el.tag
    if tag == "String":
        return _text(el)
    if tag == "Number":
        s = _text(el)
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s
    if tag == "Array":
        return [_parse_typed_value(child) for child in list(el)]
    if tag == "Object":
        obj: dict[str, object] = {}
        for child in list(el):
            # In Object, child tags are keys.
            if len(list(child)) == 0:
                obj[child.tag] = _text(child)
            elif len(list(child)) == 1 and list(child)[0].tag in {"String", "Number", "Array", "Object"}:
                obj[child.tag] = _parse_typed_value(list(child)[0])
            else:
                # Nested structures without explicit typed wrapper.
                obj[child.tag] = _parse_struct(child)
        return obj
    # Fallback: treat as structure.
    return _parse_struct(el)


def parse_connect_xml(path: Path) -> list[ConnectMessage]:
    tree = ET.parse(path)
    root = tree.getroot()

    out: list[ConnectMessage] = []
    for msg in root.findall("Message"):
        t_ms = int(float(msg.attrib.get("time", "0")))
        method_el = msg.find("Method")
        method = _text(method_el)

        args: list[object] = []
        for child in list(msg):
            if child.tag == "Method":
                continue
            args.append(_parse_typed_value(child))

        if method:
            out.append(ConnectMessage(t_ms=t_ms, method=method, args=args))

    return out

